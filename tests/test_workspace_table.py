import copy
import pytest
from fastapi.testclient import TestClient
from conftest import build
from MY.app import create_app
from shared.source import Source
from shared.tools import Tools
from test_card_cache import edit
from test_chat_tables import request_table
from test_dashboard_cards import second_dashboard


def test_card_table_uses_its_own_source_filters_and_calculations(client, monkeypatch):
    first = build(client)
    ident = first["messages"][-1]["id"]
    second = second_dashboard(client)
    unchanged = copy.deepcopy(second["dataset"])
    edit(client, ident, {"regions": ["Центральный"]})
    edit(
        client,
        ident,
        {"action": "add", "calculation": {"name": "gap", "label": "Разница", "expression": "fact-plan"}},
        "calculation",
    )
    monkeypatch.setattr(Source, "load", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("No fetch")))
    before = client.get("/api/state").json()
    who = "web:" + client.cookies["puzzle_session"]
    stored = client.app.state.store.load(who)
    table = client.get(f"/api/cards/{ident}/table?limit=25").json()
    assert table["dataset"]["id"] == first["dataset"]["id"] != unchanged["id"]
    assert table["filters"]["regions"] == ["Центральный"]
    assert table["calculations"][0]["name"] == "gap"
    assert all(
        r["region"] == "Центральный" and r["gap"] == pytest.approx(r["fact"] - r["plan"], abs=0.01)
        for r in table["dataset"]["preview"]
    )
    assert client.app.state.store.load(who) == stored
    assert before["dataset"] == unchanged


def test_card_table_pagination_bounds_and_owner_scope(client):
    initial = build(client)
    ident = initial["messages"][-1]["id"]
    first = client.get(f"/api/cards/{ident}/table?limit=25").json()["dataset"]
    second = client.get(f"/api/cards/{ident}/table?offset=25&limit=25").json()["dataset"]
    assert first["preview"] != second["preview"] and second["offset"] == 25
    last = client.get(f"/api/cards/{ident}/table?offset=999999&limit=25").json()["dataset"]
    assert last["offset"] + len(last["preview"]) == first["rows"]
    for query in ["offset=-1", "limit=0", "limit=201"]:
        assert client.get(f"/api/cards/{ident}/table?{query}").status_code == 422
    with TestClient(client.app) as other:
        other.get("/")
        assert other.get(f"/api/cards/{ident}/table").status_code == 400
    client.headers["X-Conversation-ID"] = client.post("/api/conversations", json={"title": "Другой"}).json()[
        "id"
    ]
    assert client.get(f"/api/cards/{ident}/table").status_code == 400


def test_requested_table_has_editable_saved_context_and_survives_restart(client, settings):
    initial = request_table(client)
    reply = initial["messages"][-1]
    assert reply["workspace_view"] == "table" and reply["dashboard"]["saved_id"]
    ident = reply["id"]
    expected = client.get(f"/api/cards/{ident}/table").json()
    assert expected["dataset"]["rows"] == reply["table"]["total"]
    second_dashboard(client)
    with TestClient(create_app(settings)) as restarted:
        restarted.cookies.update(client.cookies)
        assert restarted.get(f"/api/cards/{ident}/table").json() == expected
        assert (
            next(m for m in restarted.get("/api/state").json()["messages"] if m["id"] == ident)[
                "workspace_view"
            ]
            == "table"
        )


def test_calculation_reply_opens_table_even_if_model_omits_table_flag(client, monkeypatch):
    build(client)
    service = client.app.state.chat_service

    def run(who, state, text, **kwargs):
        Tools(client.app.state.settings, client.app.state.store, who, state).invoke(
            "compute_column",
            {"action": "add", "calculation": {"name": "gap", "label": "Разница", "expression": "fact-plan"}},
        )
        service._reply(state, "Колонка добавлена", "calculation")
        return {
            "tools": ["compute_column"],
            "presentation": {"charts": [], "table": False, "exports": [], "controls": []},
        }

    monkeypatch.setattr(service.agent, "run", run)
    revision = client.get("/api/state").json()["revision"]
    result = client.post("/api/chat", json={"revision": revision, "message": "Добавь колонку разницы"}).json()
    message = result["messages"][-1]
    assert message["workspace_view"] == "table"
    assert "gap" in [c["name"] for c in message["table"]["columns"]]
    assert client.get(f"/api/cards/{message['id']}/table").json()["calculations"][0]["name"] == "gap"


def test_empty_card_table_keeps_schema(client):
    initial = build(client)
    ident = initial["messages"][-1]["id"]
    edit(client, ident, {"regions": ["Нет региона"]})
    table = client.get(f"/api/cards/{ident}/table?offset=99999").json()["dataset"]
    assert table["rows"] == 0 and table["preview"] == [] and table["offset"] == 0 and table["columns"]


def test_delete_last_calculation_can_be_undone_from_table(client):
    ident = build(client)["messages"][-1]["id"]
    edit(
        client,
        ident,
        {"action": "add", "calculation": {"name": "gap", "label": "Разница", "expression": "fact-plan"}},
        "calculation",
    )
    edit(client, ident, {"action": "delete", "name": "gap"}, "calculation")
    empty = client.get(f"/api/cards/{ident}/table").json()
    assert empty["calculations"] == [] and empty["can_undo"]
    edit(client, ident, {"action": "undo"}, "calculation")
    restored = client.get(f"/api/cards/{ident}/table").json()
    assert restored["calculations"][0]["name"] == "gap"
    assert "gap" in [c["name"] for c in restored["dataset"]["columns"]]
