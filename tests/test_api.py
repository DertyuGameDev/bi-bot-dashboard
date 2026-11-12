from fastapi.testclient import TestClient
from MY.app import create_app
from shared.formatting import markdown_html
from conftest import build, post


def test_full_workflow_filters_calculations_save_reopen_copy(client, settings):
    initial = build(client)
    assert len(initial["charts"]) == 4
    region = initial["options"]["regions"][0]
    result = post(client, "/api/filters", {"regions": [region]}).json()
    assert result["kpis"]["fact"] < initial["kpis"]["fact"]
    assert result["debug"]["model"] == initial["debug"]["model"]
    assert len(result["options"]["outlets"]) == 1
    assert all(row["region"] == region for row in result["dataset"]["preview"])
    result = post(client, "/api/filters", {"dimension": "outlet"}).json()
    assert result["charts"][2]["spec"]["x"] == "outlet"
    calc = {
        "action": "add",
        "calculation": {"name": "completion", "label": "Выполнение", "expression": "fact / plan * 100"},
    }
    assert post(client, "/api/calculation", calc).status_code == 200
    result = post(
        client, "/api/chart", {"index": 3, "changes": {"sort": "completion", "zoom": [-0.4, 0.6, 0.0, 100.0]}}
    ).json()
    assert result["charts"][2]["spec"]["y"] == ["completion"]
    saved = post(client, "/api/dashboards/save", {"title": "Продажи Q3"}).json()
    ident = saved["saved_id"]
    # Simulate app restart with same SQLite and browser session.
    with TestClient(create_app(settings)) as restarted:
        restarted.cookies.update(client.cookies)
        restarted.headers.update(client.headers)
        snapshot = restarted.get("/api/state").json()
        assert snapshot["filters"] == saved["filters"]
        opened = post(restarted, f"/api/dashboards/{ident}/open", {}).json()
        assert opened["kpis"] == saved["kpis"]
        assert opened["calculations"] == saved["calculations"]
        assert opened["charts"][2]["spec"]["zoom"] == [-0.4, 0.6, 0.0, 100.0]
        copy = post(restarted, "/api/dashboards/save", {"title": "Копия", "as_new": True}).json()
        assert copy["saved_id"] != ident
        assert len(restarted.get("/api/dashboards").json()["items"]) == 2


def test_invalid_formula_leaves_last_valid_state(client):
    initial = build(client)
    result = post(
        client,
        "/api/calculation",
        {"action": "add", "calculation": {"name": "unsafe", "label": "X", "expression": '__import__("os")'}},
    )
    assert result.status_code == 400
    current = client.get("/api/state").json()
    assert current["revision"] == initial["revision"]
    assert current["calculations"] == []
    assert current["kpis"] == initial["kpis"]


def test_contextual_undo_and_dependency_delete(client):
    build(client)
    for name, expr in [("delta", "fact-plan"), ("percent", "delta/plan*100")]:
        assert (
            post(
                client,
                "/api/calculation",
                {"action": "add", "calculation": {"name": name, "label": name, "expression": expr}},
            ).status_code
            == 200
        )
    assert post(client, "/api/calculation", {"action": "delete", "name": "delta"}).status_code == 400
    out = post(client, "/api/calculation", {"action": "undo"}).json()
    assert [x["name"] for x in out["calculations"]] == ["delta"]


def test_empty_filter_and_stale_revision(client):
    initial = build(client)
    response = post(client, "/api/filters", {"regions": ["Нет такого региона"]})
    assert response.status_code == 200, response.text
    assert response.json()["kpis"]["rows"] == 0
    assert len(response.json()["charts"]) == 4
    stale = client.post("/api/filters", json={"revision": initial["revision"], "arguments": {"regions": []}})
    assert stale.status_code == 409


def test_text_question_does_not_create_chart(client):
    result = client.post("/api/chat", json={"revision": 0, "message": "Какова сумма продаж?"})
    assert result.status_code == 200
    assert result.json()["charts"] == []
    assert result.json()["messages"][-1]["intent"] == "text"


def test_isolation_and_snapshot_crud(client, settings):
    build(client)
    result = post(client, "/api/dashboards/save", {"title": "Продажи Q3"}).json()
    ident = result["saved_id"]
    with TestClient(create_app(settings)) as other:
        other.get("/")
        other.headers["X-Requested-With"] = "puzzle-rpa"
        assert other.get("/api/dashboards").json()["items"] == []
        assert post(other, f"/api/dashboards/{ident}/open", {}).status_code == 400
        assert other.delete("/api/dashboards/" + ident).status_code == 400
    assert client.patch("/api/dashboards/" + ident, json={"title": "Финал"}).status_code == 200
    assert client.get("/api/dashboards?search=Финал&sort=title").json()["items"][0]["title"] == "Финал"
    assert client.delete("/api/dashboards/" + ident).status_code == 200


def test_export_and_csrf(client):
    build(client)
    csv = client.get("/api/export/csv")
    assert csv.status_code == 200 and "fact" in csv.text
    html = client.get("/api/export/html")
    assert html.status_code == 200 and "Matplotlib" in html.text
    svg = client.get("/api/export/svg?index=1")
    assert svg.status_code == 200 and "data-mark" in svg.text
    client.headers.pop("X-Requested-With")
    assert post(client, "/api/filters", {}).status_code == 403


def test_markdown_format_escaping():
    text = markdown_html("**Факт** <script>alert(1)</script> & 10")
    assert "<strong>Факт</strong>" in text and "<script>" not in text and "&lt;script&gt;" in text


def test_saved_snapshot_contains_materialized_columns_and_kpis(client, settings):
    build(client)
    post(
        client,
        "/api/calculation",
        {"action": "add", "calculation": {"name": "pct", "label": "Процент", "expression": "fact/plan*100"}},
    )
    out = post(client, "/api/dashboards/save", {"title": "Materialized"}).json()
    owner = "web:" + client.cookies["puzzle_session"]
    state = client.app.state.store.open(owner, out["saved_id"])
    assert state["snapshot"]["rows"]
    assert "pct" in state["snapshot"]["rows"][0]
    assert state["snapshot"]["kpis"] == out["kpis"]


def test_filter_outside_loaded_period_rejected(client):
    build(client)
    result = post(client, "/api/filters", {"start": "2000-01-01"})
    assert result.status_code == 400
    assert "вне загруженной" in result.json()["detail"]


def test_empty_filtered_snapshot_can_be_restored(client):
    build(client)
    post(client, "/api/filters", {"regions": ["Не существует"]})
    result = post(client, "/api/dashboards/save", {"title": "Пустая выборка"})
    assert result.status_code == 200, result.text
    ident = result.json()["saved_id"]
    opened = post(client, f"/api/dashboards/{ident}/open", {})
    assert opened.status_code == 200, opened.text
    assert opened.json()["kpis"]["rows"] == 0
    assert len(opened.json()["charts"]) == 4
