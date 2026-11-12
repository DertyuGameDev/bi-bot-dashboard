"""Saved chat results remain independently accessible without rerunning the model/source."""

from fastapi.testclient import TestClient
from conftest import build, post
from MY.app import create_app
from shared.source import Source
from shared.tools import Tools


def owner(client):
    return "web:" + client.cookies["puzzle_session"]


def test_zoom_is_local_to_one_chart_and_survives_return_from_library(client):
    initial = build(client)
    zoom = [-0.4, 3.6, 0, 2e7]
    changed = post(client, "/api/chart", {"index": 1, "changes": {"zoom": zoom}}).json()
    assert changed["kpis"] == initial["kpis"] and changed["filters"] == initial["filters"]
    assert changed["saved_id"] == initial["saved_id"]
    for before, after in zip(initial["charts"][1:], changed["charts"][1:]):
        assert after["spec"] == before["spec"]
        assert after["metadata"]["axes"] == before["metadata"]["axes"]
        assert after["metadata"]["tips"] == before["metadata"]["tips"]
    restored = post(client, f"/api/dashboards/{initial['saved_id']}/open", {}).json()
    assert restored["charts"][0]["spec"]["zoom"] == zoom
    assert all(chart["spec"]["zoom"] is None for chart in restored["charts"][1:])
    assert restored["kpis"] == initial["kpis"]
    filtered = post(client, "/api/filters", {"regions": ["Центральный"]}).json()
    assert all(chart["spec"]["zoom"] is None for chart in filtered["charts"])
    assert filtered["kpis"]["fact"] < initial["kpis"]["fact"]
    assert filtered["messages"][-1]["dashboard"]["kpis"] == filtered["kpis"]


def chat(client, text, request_id=None):
    response = client.post(
        "/api/chat",
        json={
            "revision": client.get("/api/state").json()["revision"],
            "message": text,
            "request_id": request_id,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_new_visual_replies_autosave_distinct_snapshots_with_their_own_query(client):
    first = build(client)
    a = first["saved_id"]
    assert a and first["messages"][-1]["dashboard"]["saved"]
    assert first["messages"][-1]["dashboard"]["saved_id"] == a
    second = chat(client, "Покажи только Центральный регион", "central-1")
    b = second["saved_id"]
    assert b != a
    store = client.app.state.store
    assert store.open(owner(client), a)["filters"]["regions"] == []
    assert store.open(owner(client), b)["filters"]["regions"] == ["Центральный"]
    items = client.get("/api/dashboards?search=Центральный").json()["items"]
    assert [item["id"] for item in items] == [b]
    assert items[0]["query"] == "Покажи только Центральный регион"
    chat(client, "Покажи только Центральный регион", "central-1")
    chat(client, "Какая сумма продаж?")
    assert len(client.get("/api/dashboards").json()["items"]) == 2


def test_filter_changes_autosave_same_snapshot_and_explicit_save_renames_or_copies(client):
    first = build(client)
    ident = first["saved_id"]
    changed = post(client, "/api/filters", {"regions": ["Центральный"]}).json()
    assert changed["saved_id"] == ident
    assert changed["messages"][-1]["dashboard"]["saved"] is True
    original = client.app.state.store.open(owner(client), ident)
    assert original["filters"] == changed["filters"]
    assert original["snapshot"]["kpis"] == changed["kpis"]
    saved = post(client, "/api/dashboards/save", {"title": "Центральный · новый выбор"}).json()
    assert saved["saved_id"] == ident
    assert saved["messages"][-1]["dashboard"]["saved"]
    assert saved["messages"][-1]["dashboard"]["title"] == "Центральный · новый выбор"
    assert len(client.get("/api/dashboards").json()["items"]) == 1
    copied = post(client, "/api/dashboards/save", {"title": "Копия", "as_new": True}).json()
    assert copied["saved_id"] != ident
    assert len(client.get("/api/dashboards").json()["items"]) == 2
    assert {item["query"] for item in client.get("/api/dashboards").json()["items"]} == {
        "Собери дашборд по продажам за последний год"
    }


def test_autosave_chart_formula_and_filters_restore_after_restart_without_manual_save(
    client, settings, monkeypatch
):
    initial = build(client)
    ident = initial["saved_id"]
    post(
        client,
        "/api/calculation",
        {
            "action": "add",
            "calculation": {"name": "ratio", "label": "Выполнение", "expression": "fact/plan*100"},
        },
    )
    changed = post(
        client,
        "/api/filters",
        {
            "regions": ["Центральный"],
            "metric": "completion",
            "granularity": "quarter",
            "value_mode": "dynamics",
        },
    ).json()
    changed = post(
        client, "/api/chart", {"index": 1, "changes": {"hidden": ["plan"], "zoom": [-0.4, 2.6, 0, 100]}}
    ).json()
    assert changed["saved_id"] == ident and changed["messages"][-1]["dashboard"]["saved"]
    assert len(client.get("/api/dashboards").json()["items"]) == 1

    def forbidden(*args, **kwargs):
        raise AssertionError("Restoring autosave must use frozen data")

    monkeypatch.setattr(Source, "load", forbidden)
    with TestClient(create_app(settings)) as restarted:
        restarted.cookies.update(client.cookies)
        restarted.headers.update(client.headers)
        monkeypatch.setattr(restarted.app.state.chat_service.agent, "run", forbidden)
        reopened = post(restarted, f"/api/dashboards/{ident}/open", {}).json()
    assert reopened["filters"] == changed["filters"]
    assert reopened["calculations"] == changed["calculations"]
    assert reopened["kpis"] == changed["kpis"]
    assert reopened["charts"][0]["spec"]["hidden"] == ["plan"]
    assert reopened["charts"][0]["spec"]["zoom"] == [-0.4, 2.6, 0, 100]


def test_auto_edits_keep_other_dashboard_copy_and_original_query(client):
    first = build(client)
    copy = post(client, "/api/dashboards/save", {"title": "Копия", "as_new": True}).json()
    post(client, "/api/filters", {"regions": ["Центральный"]})
    store = client.app.state.store
    assert store.open(owner(client), first["saved_id"])["filters"] == first["filters"]
    assert store.open(owner(client), copy["saved_id"])["filters"]["regions"] == ["Центральный"]
    assert {item["query"] for item in store.list(owner(client))} == {
        "Собери дашборд по продажам за последний год"
    }
    # A stale request cannot save over a more recent accepted edit.
    before = store.open(owner(client), copy["saved_id"])
    response = client.post("/api/filters", json={"revision": copy["revision"], "arguments": {"regions": []}})
    assert response.status_code == 409
    assert store.open(owner(client), copy["saved_id"]) == before


def test_autosave_failure_rolls_back_library_and_session_together(client, monkeypatch):
    initial = build(client)
    store = client.app.state.store
    saved = store.open(owner(client), initial["saved_id"])
    current = store.load(owner(client))
    original = store._save

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise ValueError("Проверяемый сбой сохранения")

    monkeypatch.setattr(store, "_save", fail)
    failed = post(client, "/api/filters", {"regions": ["Центральный"]})
    assert failed.status_code == 400
    assert store.open(owner(client), initial["saved_id"]) == saved
    assert store.load(owner(client)) == current


def test_legacy_unsaved_dashboard_gets_one_entry_on_first_edit(client):
    build(client)
    store = client.app.state.store
    state = store.load(owner(client))
    ident = state["saved_id"]
    client.delete(f"/api/dashboards/{ident}")
    assert client.get("/api/dashboards").json()["items"] == []
    first = post(client, "/api/filters", {"top_n": 20}).json()
    assert first["saved_id"] and first["saved_id"] != ident
    second = post(client, "/api/filters", {"top_n": 10}).json()
    assert second["saved_id"] == first["saved_id"]
    assert second["messages"][-1]["dashboard"]["saved"]
    assert len(store.list(owner(client))) == 1


def test_refresh_autosaves_new_data_version_and_preserves_user_title(client):
    initial = build(client)
    client.patch(f"/api/dashboards/{initial['saved_id']}", json={"title": "Мой отчёт"})
    refreshed = post(client, "/api/refresh", {}).json()
    snapshot = client.app.state.store.open(owner(client), initial["saved_id"])
    assert snapshot["dashboard"]["title"] == "Мой отчёт"
    assert snapshot["dataset"]["id"] == refreshed["dataset"]["id"] != initial["dataset"]["id"]
    assert snapshot["snapshot"]["kpis"] == refreshed["kpis"]
    assert len(client.get("/api/dashboards").json()["items"]) == 1


def test_library_open_appends_in_chat_preserves_history_and_restores_after_restart(
    client, settings, monkeypatch
):
    build(client)
    post(
        client,
        "/api/calculation",
        {"action": "add", "calculation": {"name": "pct", "label": "Процент", "expression": "fact/plan*100"}},
    )
    post(client, "/api/chart", {"index": 1, "changes": {"zoom": [-0.4, 2.6, 0, 1000000], "hidden": ["plan"]}})
    saved = post(client, "/api/dashboards/save", {"title": "Первый снимок"}).json()
    second = chat(client, "Покажи только Центральный регион")
    history = second["messages"]
    actions = client.app.state.store.load(owner(client))["chat_actions"]

    def forbidden(*args, **kwargs):
        raise AssertionError("Opening a snapshot must not call the source or model")

    monkeypatch.setattr(Source, "load", forbidden)
    monkeypatch.setattr(client.app.state.chat_service.agent, "run", forbidden)
    opened = post(client, f"/api/dashboards/{saved['saved_id']}/open", {}).json()
    assert opened["messages"][:-1] == history
    assert opened["messages"][-1]["id"] not in {m["id"] for m in history}
    assert opened["messages"][-1]["dashboard"]["saved_id"] == saved["saved_id"]
    assert opened["filters"] == saved["filters"]
    assert opened["calculations"] == saved["calculations"]
    assert opened["kpis"] == saved["kpis"]
    assert opened["charts"][0]["spec"]["zoom"] == [-0.4, 2.6, 0, 1000000]
    assert opened["charts"][0]["spec"]["hidden"] == ["plan"]
    assert client.app.state.store.load(owner(client))["chat_actions"] == actions
    with TestClient(create_app(settings)) as restarted:
        restarted.cookies.update(client.cookies)
        restarted.headers.update(client.headers)
        assert len(restarted.get("/api/dashboards").json()["items"]) == 2
        restored = post(restarted, f"/api/dashboards/{second['saved_id']}/open", {}).json()
        assert restored["kpis"] == second["kpis"]
        assert restored["filters"] == second["filters"]


def test_delete_clears_chat_references_and_rename_is_restored(client):
    saved = build(client)
    ident = saved["saved_id"]
    assert client.patch(f"/api/dashboards/{ident}", json={"title": "Мой отчёт"}).status_code == 200
    opened = post(client, f"/api/dashboards/{ident}/open", {}).json()
    assert opened["messages"][-1]["dashboard"]["title"] == "Мой отчёт"
    assert client.delete(f"/api/dashboards/{ident}").status_code == 200
    state = client.get("/api/state").json()
    assert state["saved_id"] is None
    assert all(not m["dashboard"].get("saved_id") for m in state["messages"] if m.get("dashboard"))
    assert client.get("/api/dashboards").json()["items"] == []
    assert post(client, f"/api/dashboards/{ident}/open", {}).status_code == 400


def test_tool_open_keeps_current_chat_buttons_and_foreign_id_cannot_be_overwritten(client):
    first = build(client)
    store = client.app.state.store
    state = store.load(owner(client))
    state["chat_actions"]["latest-control"] = {"operation": "saved", "arguments": {}}
    tools = Tools(client.app.state.settings, store, owner(client), state)
    tools.invoke("open_dashboard", {"id": first["saved_id"]})
    assert "latest-control" in state["chat_actions"]
    other = store.load("other-owner")
    other.update(dataset=state["dataset"], dashboard=state["dashboard"], saved_id=first["saved_id"])
    result = store.save("other-owner", other, "Other snapshot")
    assert result["id"] != first["saved_id"]
    assert store.open(owner(client), first["saved_id"])["dashboard"]["title"] == first["dashboard"]["title"]
