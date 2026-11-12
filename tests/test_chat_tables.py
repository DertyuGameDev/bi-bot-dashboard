import pytest
from fastapi.testclient import TestClient
from conftest import post
from MY.app import create_app
from shared.analytics import materialize
from shared.source import records


def request_table(client):
    revision = client.get("/api/state").json()["revision"]
    result = client.post("/api/chat", json={"revision": revision, "message": "Покажи таблицу продаж"})
    assert result.status_code == 200, result.text
    return result.json()


def test_all_table_rows_can_be_paged_without_truncation(client):
    result = request_table(client)
    table = result["messages"][-1]["table"]
    assert len(table["rows"]) == 25 and table["total"] > 25
    assert all({"name", "label", "type"} <= set(c) for c in table["columns"])
    rows = []
    for offset in range(0, table["total"], 25):
        page = client.get(f"/api/chat/tables/{table['id']}?offset={offset}&limit=25").json()
        assert page["offset"] == offset and page["total"] == table["total"]
        rows.extend(page["rows"])
    state = client.app.state.store.load("web:" + client.cookies["puzzle_session"])
    df, _, _ = materialize(state)
    assert rows == records(df[[c["name"] for c in table["columns"]]])
    assert sum(r["fact"] for r in rows if r["fact"] is not None) == pytest.approx(result["kpis"]["fact"])


def test_table_snapshot_is_stable_after_filters_change_and_app_restart(client, settings):
    initial = request_table(client)
    table = initial["messages"][-1]["table"]
    changed = post(client, "/api/filters", {"regions": ["Центральный"]}).json()
    assert changed["dataset"]["rows"] < table["total"]
    assert changed["messages"][-1]["table"] == table
    with TestClient(create_app(settings)) as restarted:
        restarted.cookies.update(client.cookies)
        page = restarted.get(f"/api/chat/tables/{table['id']}").json()
        assert page == table
        assert restarted.get("/api/state").json()["messages"][-1]["table"] == table


def test_table_pagination_is_owner_scoped_and_rejects_non_table_media(client):
    result = request_table(client)
    reply = result["messages"][-1]
    other = TestClient(client.app)
    try:
        other.get("/")
        response = other.get(f"/api/chat/tables/{reply['table']['id']}")
        assert response.status_code == 400
        assert "rows" not in response.json()
    finally:
        other.close()
    assert client.get(f"/api/chat/tables/{reply['attachments'][0]['id']}").status_code == 400


def test_table_page_bounds_and_last_page(client):
    table = request_table(client)["messages"][-1]["table"]
    last = client.get(f"/api/chat/tables/{table['id']}?offset=999999&limit=25").json()
    assert last["offset"] + len(last["rows"]) == table["total"]
    assert 1 <= len(last["rows"]) <= 25
    assert client.get(f"/api/chat/tables/{table['id']}?offset=-1").status_code == 422
    assert client.get(f"/api/chat/tables/{table['id']}?limit=0").status_code == 422
    assert client.get(f"/api/chat/tables/{table['id']}?limit=201").status_code == 422


def test_empty_selection_has_an_inline_table_with_columns(client):
    request_table(client)
    post(client, "/api/filters", {"regions": ["Нет такого региона"]})
    result = request_table(client)
    table = result["messages"][-1]["table"]
    assert table["columns"] and table["rows"] == [] and table["total"] == 0
    assert client.get(f"/api/chat/tables/{table['id']}").json() == table


def test_inline_values_keep_original_strings_while_csv_is_escaped(client):
    request_table(client)
    store = client.app.state.store
    owner = "web:" + client.cookies["puzzle_session"]
    state = store.load(owner)
    row = next(r for r in state["dataset"]["rows"] if r["date"].startswith("2025"))
    row["outlet"] = "=SUM(1,2) <b>текст</b>"
    state["snapshot"] = None
    store.commit(owner, state, state["revision"])
    reply = request_table(client)["messages"][-1]
    assert any(r["outlet"] == "=SUM(1,2) <b>текст</b>" for r in reply["table"]["rows"])
    csv = client.get(reply["attachments"][0]["url"]).text
    assert "'=SUM(1,2)" in csv
