"""Returning to a dashboard explicitly selects the target of future analysis."""

import json
from fastapi.testclient import TestClient
from conftest import build, post
from shared.source import Source
from MY.app import create_app


def owner(client):
    return "web:" + client.cookies["puzzle_session"]


def chat(client, message):
    state = client.get("/api/state").json()
    response = client.post("/api/chat", json={"revision": state["revision"], "message": message})
    assert response.status_code == 200, response.text
    return response.json()


def two_dashboards(client):
    first = build(client)
    second = chat(client, "Покажи только Центральный регион")
    assert first["saved_id"] != second["saved_id"]
    return first, second


def resume(client, message_id):
    state = client.get("/api/state").json()
    return client.post(
        "/api/workspace/continue",
        json={"revision": state["revision"], "arguments": {}, "message_id": message_id},
    )


def test_resume_a_card_selects_its_table_and_formulas_without_an_extra_message(client, monkeypatch):
    first, second = two_dashboards(client)

    def forbidden(*args, **kwargs):
        raise AssertionError("Continuing a saved card must not query the model or database")

    monkeypatch.setattr(Source, "load", forbidden)
    monkeypatch.setattr(client.app.state.chat_service.agent, "run", forbidden)
    selected = resume(client, first["messages"][-1]["id"])
    assert selected.status_code == 200, selected.text
    selected = selected.json()
    assert selected["workspace"]["saved_id"] == first["saved_id"]
    assert selected["workspace"]["message_id"] == first["messages"][-1]["id"]
    assert selected["filters"] == first["filters"] and selected["kpis"] == first["kpis"]
    assert len(selected["messages"]) == len(second["messages"])
    assert selected["workspace"]["created"] and selected["workspace"]["updated"]


def test_followup_updates_the_chosen_entry_keeps_original_query_and_informs_model(client, monkeypatch):
    first, second = two_dashboards(client)
    resume(client, first["messages"][-1]["id"])
    seen = []
    original = client.app.state.chat_service.agent.adapter.step

    def observe(prompt):
        seen.append(json.loads(prompt)["context"])
        return original(prompt)

    monkeypatch.setattr(client.app.state.chat_service.agent.adapter, "step", observe)
    changed = chat(client, "Покажи только Центральный регион")
    assert changed["saved_id"] == first["saved_id"]
    assert changed["workspace"]["saved_id"] == first["saved_id"]
    assert changed["workspace"]["message_id"] == changed["messages"][-1]["id"]
    assert changed["filters"]["regions"] == ["Центральный"]
    assert seen[0]["workspace"]["saved_id"] == first["saved_id"]
    store = client.app.state.store
    assert len(store.list(owner(client))) == 2
    assert store.open(owner(client), second["saved_id"])["filters"] == second["filters"]
    item = next(d for d in store.list(owner(client)) if d["id"] == first["saved_id"])
    assert item["query"] == "Собери дашборд по продажам за последний год"
    assert changed["workspace"]["query"] == item["query"]


def test_rebuilding_a_chosen_dashboard_does_not_silently_create_another_entry(client):
    first = build(client)
    post(client, f"/api/dashboards/{first['saved_id']}/open", {})
    rebuilt = chat(client, "Собери дашборд по продажам")
    assert rebuilt["saved_id"] == first["saved_id"]
    assert len(client.get("/api/dashboards").json()["items"]) == 1


def test_formula_changes_and_workspace_restore_after_restart(client, settings, monkeypatch):
    first, second = two_dashboards(client)
    resume(client, first["messages"][-1]["id"])
    changed = post(
        client,
        "/api/calculation",
        {"action": "add", "calculation": {"name": "gap", "label": "Разница", "expression": "fact-plan"}},
    ).json()
    assert changed["saved_id"] == first["saved_id"]

    def forbidden(*args, **kwargs):
        raise AssertionError("No source fetch on restart")

    monkeypatch.setattr(Source, "load", forbidden)
    with TestClient(create_app(settings)) as restarted:
        restarted.cookies.update(client.cookies)
        restarted.headers.update(client.headers)
        current = restarted.get("/api/state").json()
    assert current["workspace"]["saved_id"] == first["saved_id"]
    assert current["calculations"] == changed["calculations"]
    assert current["kpis"] == changed["kpis"]
    stored = client.app.state.store.open(owner(client), first["saved_id"])
    assert "gap" in stored["snapshot"]["columns"]
    assert client.app.state.store.open(owner(client), second["saved_id"])["calculations"] == []


def test_library_copy_is_selected_and_preserves_original(client):
    first = build(client)
    copied = post(client, f"/api/dashboards/{first['saved_id']}/open", {"as_new": True}).json()
    assert copied["workspace"]["saved_id"] == copied["saved_id"] != first["saved_id"]
    assert "копия" in copied["workspace"]["title"]
    changed = post(client, "/api/filters", {"regions": ["Центральный"]}).json()
    assert changed["workspace"]["saved_id"] == copied["saved_id"]
    original = client.app.state.store.open(owner(client), first["saved_id"])
    assert original["filters"] == first["filters"]
    items = client.get("/api/dashboards").json()["items"]
    assert len(items) == 2 and items[0]["query"] == items[1]["query"]


def test_named_copy_switches_workspace_and_new_analysis_preserves_history(client):
    first = build(client)
    post(client, f"/api/dashboards/{first['saved_id']}/open", {})
    copied = post(client, "/api/dashboards/save", {"title": "Эксперимент", "as_new": True}).json()
    assert copied["workspace"]["title"] == "Эксперимент"
    assert copied["workspace"]["saved_id"] == copied["saved_id"] != first["saved_id"]
    cleared = post(client, "/api/workspace/new", {}).json()
    assert cleared["workspace"] is None and cleared["dataset"] is None and cleared["saved_id"] is None
    assert cleared["messages"] == copied["messages"]
    next_one = build(client)
    assert next_one["saved_id"] not in {first["saved_id"], copied["saved_id"]}
    assert len(client.get("/api/dashboards").json()["items"]) == 3


def test_other_card_edit_does_not_switch_the_selected_workspace(client):
    first, second = two_dashboards(client)
    selected = resume(client, first["messages"][-1]["id"]).json()
    changed = client.post(
        "/api/filters",
        json={
            "revision": selected["revision"],
            "message_id": second["messages"][-1]["id"],
            "arguments": {"regions": ["Уральский"]},
        },
    ).json()
    assert changed["workspace"]["saved_id"] == first["saved_id"]
    assert changed["filters"] == first["filters"]
    assert changed["messages"][-1]["dashboard"]["filters"]["regions"] == ["Уральский"]


def test_selected_name_and_deletion_are_reflected_in_context(client):
    first = build(client)
    post(client, f"/api/dashboards/{first['saved_id']}/open", {})
    client.patch(f"/api/dashboards/{first['saved_id']}", json={"title": "Утренний отчёт"})
    assert client.get("/api/state").json()["workspace"]["title"] == "Утренний отчёт"
    client.delete(f"/api/dashboards/{first['saved_id']}")
    assert client.get("/api/state").json()["workspace"] is None


def test_resume_checks_ownership_and_revision(client):
    first = build(client)
    ident = first["messages"][-1]["id"]
    with TestClient(client.app) as stranger:
        stranger.get("/")
        stranger.headers["X-Requested-With"] = "puzzle-rpa"
        assert resume(stranger, ident).status_code == 400
    assert resume(client, "missing").status_code == 400
    stale = client.post("/api/workspace/continue", json={"revision": 0, "message_id": ident, "arguments": {}})
    assert stale.status_code == 409


def test_library_search_is_russian_case_insensitive_and_matches_original_query(client):
    first = build(client)
    client.patch(f"/api/dashboards/{first['saved_id']}", json={"title": "Ёлка · Утро"})
    for term in ["ЕЛКА", "елка утро", "ПОСЛЕДНИЙ ГОД", "УТРО продажам"]:
        items = client.get("/api/dashboards", params={"search": term}).json()["items"]
        assert [item["id"] for item in items] == [first["saved_id"]]
    assert client.get("/api/dashboards", params={"search": "точно отсутствует"}).json()["items"] == []


def test_an_empty_lookup_answer_does_not_discard_the_selected_work(client, monkeypatch):
    from shared.analytics import install_dataset

    first = build(client)
    selected = post(client, f"/api/dashboards/{first['saved_id']}/open", {}).json()

    def empty_answer(owner, state, text, progress=None):
        state["messages"].append({"role": "user", "content": text})
        install_dataset(state, Source(client.app.state.settings).demo("2027-01-01", "2027-12-31"))
        state["messages"].append({"role": "assistant", "content": "За 2027 год данных нет."})
        return {
            "tools": ["fetch_data"],
            "presentation": {"charts": [], "table": False, "exports": [], "controls": []},
        }

    monkeypatch.setattr(client.app.state.chat_service.agent, "run", empty_answer)
    answer = chat(client, "Есть продажи за 2027 год?")
    assert answer["messages"][-1]["content"] == "За 2027 год данных нет."
    assert answer["workspace"] == selected["workspace"]
    assert answer["dataset"]["id"] == selected["dataset"]["id"]
    assert answer["kpis"] == selected["kpis"]
