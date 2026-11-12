"""Every message keeps a separate editable dashboard, including its frozen source."""

import copy
import pytest
from fastapi.testclient import TestClient
from conftest import build, post
from shared.analytics import present
from shared.chat import ChatService
from shared.source import Source
from shared.tools import Tools, Save
from MY.app import create_app


def owner(client):
    return "web:" + client.cookies["puzzle_session"]


def test_old_svg_style_is_refreshed_at_the_same_viewport_without_changing_saved_data(client):
    from shared.charts.registry import RENDER_VERSION

    initial = build(client)
    ident = initial["messages"][-1]["id"]
    second_dashboard(client)
    store = client.app.state.store
    state = store.load(owner(client))
    message = next(m for m in state["messages"] if m["id"] == ident)
    message["dashboard"].update(render_profile=[1280, 600], render_version=RENDER_VERSION - 1)
    message["dashboard"]["charts"][0]["svg"] = "old-renderer"
    store.commit(owner(client), state, state["revision"])
    saved = store.open(owner(client), initial["saved_id"])
    result = client.get(
        "/api/state", headers={"X-Dashboard-Width": "1280", "X-Dashboard-Height": "600"}
    ).json()
    refreshed = next(m["dashboard"] for m in result["messages"] if m["id"] == ident)
    assert refreshed["render_version"] == RENDER_VERSION
    assert refreshed["charts"][0]["svg"] != "old-renderer"
    assert refreshed["filters"] == initial["filters"]
    assert refreshed["kpis"] == initial["kpis"]
    assert store.open(owner(client), initial["saved_id"])["dataset"] == saved["dataset"]
    assert store.open(owner(client), initial["saved_id"])["calculations"] == saved["calculations"]


def second_dashboard(client, year=2024):
    store, settings = client.app.state.store, client.app.state.settings
    state = store.load(owner(client))
    tools = Tools(settings, store, owner(client), state)
    tools.invoke("fetch_data", {"start": f"{year}-01-01", "end": f"{year}-12-31"})
    tools.invoke("create_dashboard", {"title": f"Второй · {year}"})
    chat = ChatService(settings, store)
    chat._append_user(state, f"Собери следующий за {year}")
    message = chat._reply(state, "Следующий дашборд")
    chat.dashboard(owner(client), state, message)
    tools.save_dashboard(Save(title=state["dashboard"]["title"], as_new=True))
    store.commit(owner(client), state, state["revision"])
    return present(state)


def edit(client, message_id, patch, path="/api/filters"):
    state = client.get("/api/state").json()
    return client.post(
        path, json={"revision": state["revision"], "message_id": message_id, "arguments": patch}
    )


def card(view, ident):
    return next(m["dashboard"] for m in view["messages"] if m["id"] == ident)


def test_old_filters_use_their_own_source_and_leave_next_dashboard_and_data_untouched(client, monkeypatch):
    first = build(client)
    old_id = first["messages"][-1]["id"]
    second = second_dashboard(client)
    next_id = second["messages"][-1]["id"]
    unchanged = copy.deepcopy(card(second, next_id))

    def forbidden(*args, **kwargs):
        raise AssertionError("Editing an older dashboard must not call the source/model")

    monkeypatch.setattr(Source, "load", forbidden)
    monkeypatch.setattr(client.app.state.chat_service.agent, "run", forbidden)
    response = edit(client, old_id, {"regions": ["Центральный"], "start": "2025-01-01", "end": "2025-12-31"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert card(result, old_id)["table_id"] == first["dataset"]["id"] != second["dataset"]["id"]
    assert card(result, old_id)["filters"]["regions"] == ["Центральный"]
    assert card(result, old_id)["kpis"]["fact"] < first["kpis"]["fact"]
    assert card(result, next_id) == unchanged
    assert result["filters"] == second["filters"] and result["kpis"] == second["kpis"]
    assert result["dataset"]["id"] == second["dataset"]["id"]
    assert result["messages"][1]["content"] == first["messages"][1]["content"]
    saved = client.app.state.store.open(owner(client), first["saved_id"])
    assert saved["filters"] == card(result, old_id)["filters"]
    assert saved["snapshot"]["kpis"] == card(result, old_id)["kpis"]


def test_cards_with_the_same_dataset_keep_independent_filters(client):
    first = build(client)
    old_id = first["messages"][-1]["id"]
    response = client.post(
        "/api/chat", json={"revision": first["revision"], "message": "Покажи только Центральный регион"}
    )
    second = response.json()
    assert second["dataset"]["id"] == first["dataset"]["id"]
    changed = edit(client, old_id, {"regions": ["Уральский"]}).json()
    assert card(changed, old_id)["filters"]["regions"] == ["Уральский"]
    assert card(changed, second["messages"][-1]["id"])["filters"]["regions"] == ["Центральный"]
    assert changed["kpis"] == second["kpis"]


def test_new_empty_source_does_not_replace_the_last_visible_dashboard(client):
    first = build(client)
    old_id = first["messages"][-1]["id"]
    store = client.app.state.store
    state = store.load(owner(client))
    Tools(client.app.state.settings, store, owner(client), state).invoke(
        "fetch_data", {"start": "2027-01-01", "end": "2027-12-31"}
    )
    ChatService(client.app.state.settings, store)._reply(state, "Нет данных за 2027")
    store.commit(owner(client), state, state["revision"])
    client.headers.update({"X-Dashboard-Width": "360", "X-Dashboard-Height": "700"})
    view = client.get("/api/state").json()
    assert card(view, old_id)["table_id"] == first["dataset"]["id"]
    assert card(view, old_id)["kpis"]["fact"] == first["kpis"]["fact"]
    assert card(view, old_id)["charts"][0]["metadata"]["width"] == 360
    assert edit(client, old_id, {"top_n": 5}).status_code == 200


def test_old_chart_settings_survive_a_restart_and_do_not_move_the_conversation(client, settings):
    first = build(client)
    old_id = first["messages"][-1]["id"]
    second = second_dashboard(client)
    changed = edit(
        client,
        old_id,
        {"index": 1, "changes": {"type": "line", "hidden": ["plan"], "zoom": [-0.4, 2.6, 0, 1e7]}},
        "/api/chart",
    )
    assert changed.status_code == 200, changed.text
    before = client.app.state.store.load(owner(client))
    with TestClient(create_app(settings)) as restarted:
        restarted.cookies.update(client.cookies)
        restarted.headers.update(client.headers)
        response = edit(restarted, old_id, {"top_n": 5})
    result = response.json()
    spec = card(result, old_id)["source_specs"][0]
    assert spec["type"] == "line" and spec["hidden"] == ["plan"]
    assert result["messages"][-1]["id"] == second["messages"][-1]["id"]
    assert len(result["messages"]) == len(before["messages"])
    assert result["saved_id"] == second["saved_id"]


def test_region_cascade_and_modes_are_local_to_the_edited_card(client):
    first = build(client)
    old_id = first["messages"][-1]["id"]
    second = second_dashboard(client)
    a = edit(
        client,
        old_id,
        {
            "regions": ["Центральный"],
            "metric": "completion",
            "granularity": "quarter",
            "value_mode": "dynamics",
        },
    ).json()
    b = post(client, "/api/filters", {"regions": ["Уральский"], "metric": "deals"}).json()
    assert card(a, old_id)["options"]["outlets"] == ["Демо-точка 1"]
    assert card(b, old_id)["filters"] == card(a, old_id)["filters"]
    assert b["filters"]["regions"] == ["Уральский"] and b["filters"]["metric"] == "deals"
    assert card(b, second["messages"][-1]["id"])["options"]["outlets"] == ["Демо-точка 3"]


def test_old_name_or_copy_applies_to_that_card_and_leaves_active_identity(client):
    first = build(client)
    old_id = first["messages"][-1]["id"]
    second = second_dashboard(client)
    result = edit(client, old_id, {"title": "Копия первого", "as_new": True}, "/api/dashboards/save").json()
    assert result["saved_id"] == second["saved_id"]
    assert card(result, old_id)["saved_id"] not in {first["saved_id"], second["saved_id"]}
    assert card(result, old_id)["title"] == "Копия первого"
    assert len(client.get("/api/dashboards").json()["items"]) == 3


def test_library_rename_is_not_reverted_when_an_old_card_is_edited(client):
    first = build(client)
    old_id = first["messages"][-1]["id"]
    second_dashboard(client)
    client.patch(f"/api/dashboards/{first['saved_id']}", json={"title": "Имя из списка"})
    changed = edit(client, old_id, {"top_n": 5}).json()
    saved = client.app.state.store.open(owner(client), first["saved_id"])
    assert saved["dashboard"]["title"] == "Имя из списка"
    assert card(changed, old_id)["saved_title"] == "Имя из списка"


def test_scoped_save_failure_is_atomic(client, monkeypatch):
    first = build(client)
    old_id = first["messages"][-1]["id"]
    second_dashboard(client)
    store = client.app.state.store
    before = store.load(owner(client))
    saved = store.open(owner(client), first["saved_id"])
    context = store.card(owner(client), before, old_id)
    original = store._save

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise ValueError("Сбой записи")

    monkeypatch.setattr(store, "_save", fail)
    response = edit(client, old_id, {"regions": ["Центральный"]})
    assert response.status_code == 400
    assert store.load(owner(client)) == before
    assert store.open(owner(client), first["saved_id"]) == saved
    assert store.card(owner(client), before, old_id) == context


def test_card_owner_and_message_membership_are_checked(client):
    first = build(client)
    old_id = first["messages"][-1]["id"]
    with TestClient(client.app) as stranger:
        stranger.get("/")
        stranger.headers["X-Requested-With"] = "puzzle-rpa"
        assert edit(stranger, old_id, {"regions": []}).status_code == 400
    assert edit(client, "missing", {"regions": []}).status_code == 400
    before = client.get("/api/state").json()
    response = client.post(
        "/api/filters",
        json={"revision": before["revision"] - 1, "message_id": old_id, "arguments": {"regions": []}},
    )
    assert response.status_code == 409


def test_legacy_card_uses_frozen_data_without_requiring_the_original_saved_entry(client):
    first = build(client)
    old_id = first["messages"][-1]["id"]
    store = client.app.state.store
    state = store.load(owner(client))
    with store.connect() as c:
        c.execute("DELETE FROM dashboard_cards WHERE owner=?", (owner(client),))
    state["messages"][-1]["dashboard"].pop("editable")
    state["messages"][-1]["dashboard"].pop("options")
    # Prepare old cards before a subsequent fetch replaces the current dataset.
    store.prepare_cards(owner(client), state)
    store.commit(owner(client), state, state["revision"])
    second_dashboard(client)
    client.delete(f"/api/dashboards/{first['saved_id']}")
    response = edit(client, old_id, {"regions": ["Центральный"]})
    assert response.status_code == 200, response.text
    assert card(response.json(), old_id)["saved"]


@pytest.mark.parametrize(
    "patch", [{"regions": ["Несуществующий"]}, {"metric": "discount"}, {"dimension": "outlet", "top_n": 1}]
)
def test_old_empty_and_alternate_views_remain_valid_independent_results(client, patch):
    first = build(client)
    old_id = first["messages"][-1]["id"]
    second = second_dashboard(client)
    response = edit(client, old_id, patch)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["kpis"] == second["kpis"]
    assert card(result, old_id)["saved"]
    assert len(card(result, old_id)["charts"]) == 4
