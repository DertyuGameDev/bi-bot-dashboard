import pytest
from conftest import build, post


def test_one_message_contains_four_interactive_charts_without_photo_attachments(client):
    result = build(client)
    reply = result["messages"][-1]
    assert reply["attachments"] == []
    assert len(reply["dashboard"]["charts"]) == 4
    assert all(c["svg"] and "data-mark" in c["svg"] for c in reply["dashboard"]["charts"])
    assert all(c["spec"]["hidden"] == [] for c in reply["dashboard"]["charts"])
    assert "<figure>" not in reply.get("markup", "")


def test_filters_and_series_update_the_same_dashboard_message_without_ai(client):
    initial = build(client)
    message = initial["messages"][-1]
    result = post(client, "/api/filters", {"regions": ["Центральный"]}).json()
    assert len(result["messages"]) == len(initial["messages"])
    assert result["messages"][-1]["id"] == message["id"]
    assert result["messages"][-1]["dashboard"]["filters"]["regions"] == ["Центральный"]
    assert result["debug"]["model"] == initial["debug"]["model"]
    result = post(client, "/api/chart", {"index": 1, "changes": {"hidden": ["plan"]}}).json()
    assert result["messages"][-1]["dashboard"]["charts"][0]["spec"]["hidden"] == ["plan"]


def test_one_period_keeps_real_columns_with_interactive_values(client):
    build(client)
    result = post(client, "/api/filters", {"start": "2025-01-01", "end": "2025-01-31"}).json()
    for index in [0, 3]:
        chart = result["messages"][-1]["dashboard"]["charts"][index]
        assert chart["status"] == "ready" and chart["svg"] and chart["metadata"]["tips"]
        assert all(p["label"] == "2025-01" for p in chart["metadata"]["tips"].values())
    assert result["messages"][-1]["dashboard"]["charts"][1]["status"] == "ready"


def test_one_outlet_and_month_keep_a_column_with_exact_value(client):
    initial = build(client)
    outlet = initial["options"]["outlets"][0]
    post(
        client,
        "/api/chart",
        {
            "index": 1,
            "changes": {
                "type": "heatmap",
                "source": "table",
                "x": "period",
                "group": "outlet",
                "y": ["fact"],
            },
        },
    )
    result = post(
        client, "/api/filters", {"outlets": [outlet], "start": "2025-01-01", "end": "2025-01-31"}
    ).json()
    chart = result["messages"][-1]["dashboard"]["charts"][0]
    assert chart["status"] == "ready" and chart["svg"]
    point = next(iter(chart["metadata"]["tips"].values()))
    assert point["value"] == result["kpis"]["fact"]
    assert outlet in point["label"] and "2025-01" in point["label"]


def test_multiple_selected_outlets_remain_distinct_comparison_groups(client):
    initial = build(client)
    selected = initial["options"]["outlets"][:2]
    assert len(selected) == 2
    result = post(
        client,
        "/api/filters",
        {
            "regions": [],
            "outlets": selected,
            "dimension": "outlet",
            "start": "2025-01-01",
            "end": "2025-01-31",
        },
    ).json()
    assert result["filters"]["outlets"] == selected
    ranking = result["messages"][-1]["dashboard"]["charts"][2]
    points = list(ranking["metadata"]["tips"].values())
    assert {p["label"] for p in points} == set(selected)
    assert sum(p["value"] for p in points if p["series"] == "fact") == pytest.approx(result["kpis"]["fact"])


def test_text_answer_does_not_resend_previous_dashboard(client):
    out = build(client)
    result = client.post(
        "/api/chat", json={"revision": out["revision"], "message": "Какая сумма продаж?"}
    ).json()
    assert len(result["charts"]) == 4
    assert "dashboard" not in result["messages"][-1]
    assert result["messages"][-1]["attachments"] == []


def test_table_is_inline_and_keeps_csv_without_rewriting_ai_text(client):
    result = client.post("/api/chat", json={"revision": 0, "message": "Покажи таблицу продаж"}).json()
    reply = result["messages"][-1]
    assert "Первые 5 строк" not in reply["content"]
    assert reply["table"]["columns"] and reply["table"]["rows"]
    assert reply["table"]["total"] == result["dataset"]["rows"]
    assert reply["attachments"][0]["kind"] == "document"
    assert "fact" in client.get(reply["attachments"][0]["url"]).text


def test_retried_request_is_idempotent(client):
    request = {"revision": 0, "message": "Собери дашборд продаж", "request_id": "my-retry-1"}
    first = client.post("/api/chat", json=request).json()
    second = client.post("/api/chat", json=request).json()
    assert first["revision"] == second["revision"] and first["messages"] == second["messages"]


def test_telegram_routes_are_removed_and_do_not_provide_an_identity(client):
    assert client.get("/tg").status_code == 404
    client.headers["X-Platform"] = "telegram"
    assert client.get("/api/state").status_code == 200  # Still the normal cookie owner.
    assert "telegram_configured" not in client.get("/api/health").json()


def test_inline_graphs_are_rendered_for_the_actual_container_width(client):
    build(client)
    client.headers["X-Chart-Width"] = "320"
    narrow = client.get("/api/state").json()["messages"][-1]["dashboard"]
    client.headers["X-Chart-Width"] = "640"
    wide = client.get("/api/state").json()["messages"][-1]["dashboard"]
    assert all(c["metadata"]["width"] == 320 for c in narrow["charts"])
    assert all(c["metadata"]["width"] == 640 for c in wide["charts"])
    assert narrow["id"] == wide["id"]
    assert [len(c["metadata"]["tips"]) for c in narrow["charts"]] == [
        len(c["metadata"]["tips"]) for c in wide["charts"]
    ]


def test_region_selection_clears_old_outlet_subset(client):
    initial = build(client)
    first = initial["options"]["outlets"][0]
    post(client, "/api/filters", {"outlets": [first]})
    region = initial["options"]["regions"][-1]
    changed = post(client, "/api/filters", {"regions": [region]}).json()
    assert changed["filters"]["outlets"] == []
    assert changed["dataset"]["rows"] > 0
    assert all(row["region"] == region for row in changed["dataset"]["preview"])


def test_last_visible_series_cannot_be_hidden(client):
    build(client)
    failed = post(client, "/api/chart", {"index": 2, "changes": {"hidden": ["fact"]}})
    assert failed.status_code == 400 and "видимой" in failed.json()["detail"]
    state = client.get("/api/state").json()
    assert state["dashboard"]["charts"][1]["hidden"] == []


def test_incompatible_region_and_outlets_are_reconciled_with_visible_notice(client):
    build(client)
    store = client.app.state.store
    state = store.load("web:" + client.cookies["puzzle_session"])
    rows = state["dataset"]["rows"]
    region = rows[0]["region"]
    elsewhere = next(r["outlet"] for r in rows if r["region"] != region)
    result = post(client, "/api/filters", {"regions": [region], "outlets": [elsewhere]}).json()
    assert result["filters"]["regions"] == [region]
    assert result["filters"]["outlets"] == []
    assert result["kpis"]["rows"] > 0
    assert "других регионов" in result["filter_notice"]
    dashboard = result["messages"][-1]["dashboard"]
    assert dashboard["kpis"] == result["kpis"]
    assert dashboard["filter_notice"] == result["filter_notice"]
    assert all(row["region"] == region for row in result["dataset"]["preview"])


def test_saved_conflict_is_fixed_on_read_and_keeps_compatible_outlets(client):
    build(client)
    store = client.app.state.store
    owner = "web:" + client.cookies["puzzle_session"]
    state = store.load(owner)
    first = state["dataset"]["rows"][0]
    elsewhere = next(r for r in state["dataset"]["rows"] if r["region"] != first["region"])
    state["filters"].update(regions=[first["region"]], outlets=[first["outlet"], elsewhere["outlet"]])
    store.commit(owner, state, state["revision"])
    result = client.get("/api/state").json()
    assert result["filters"]["outlets"] == [first["outlet"]]
    assert result["kpis"]["outlets"] == 1
    assert "В выборе осталось точек: 1" in result["filter_notice"]


def test_dashboard_size_uses_viewport_without_changing_values(client):
    initial = build(client)
    client.headers.update({"X-Dashboard-Width": "1280", "X-Dashboard-Height": "650"})
    result = client.get("/api/state").json()
    dashboard = result["messages"][-1]["dashboard"]
    assert dashboard["kpis"] == initial["kpis"]
    assert all(c["metadata"]["width"] == 632 for c in dashboard["charts"])
    assert all(120 <= c["metadata"]["height"] <= 180 for c in dashboard["charts"])
    assert [len(c["metadata"]["tips"]) for c in dashboard["charts"]] == [
        len(c["metadata"]["tips"]) for c in initial["messages"][-1]["dashboard"]["charts"]
    ]
    assert dashboard["period"] == [initial["dataset"]["query_start"], initial["dataset"]["query_end"]]
    client.headers.update({"X-Dashboard-Width": "344", "X-Dashboard-Height": "621"})
    mobile = client.get("/api/state").json()["messages"][-1]["dashboard"]
    assert all(c["metadata"]["height"] >= 240 for c in mobile["charts"])
    assert mobile["kpis"] == dashboard["kpis"]


def test_single_chart_uses_full_container_width(client):
    build(client)
    store = client.app.state.store
    owner = "web:" + client.cookies["puzzle_session"]
    state = store.load(owner)
    state["dashboard"]["charts"] = state["dashboard"]["charts"][:1]
    store.commit(owner, state, state["revision"])
    client.headers.update({"X-Dashboard-Width": "1100", "X-Dashboard-Height": "650"})
    chart = client.get("/api/state").json()["messages"][-1]["dashboard"]["charts"][0]
    assert chart["metadata"]["width"] == 1100
    assert chart["metadata"]["height"] == 360


def test_live_captions_do_not_keep_original_region_or_year_after_filtering(client):
    initial = build(client)
    original_text = initial["messages"][-1]["content"]
    post(client, "/api/chart", {"index": 2, "changes": {"title": "Выручка Сибирского региона за 2025 год"}})
    result = post(
        client, "/api/filters", {"regions": ["Центральный"], "start": "2024-01-01", "end": "2024-12-31"}
    ).json()
    message = result["messages"][-1]
    assert message["content"] == original_text
    assert message["analysis_filters"] == initial["filters"]
    assert message["dashboard"]["title"] == "Продажи · 2024"
    assert client.get("/api/state").json()["messages"][-1]["dashboard"]["title"] == "Продажи · 2024"
    assert "Сибир" not in message["dashboard"]["charts"][1]["spec"]["title"]
    assert "2025" not in message["dashboard"]["charts"][1]["spec"]["title"]
