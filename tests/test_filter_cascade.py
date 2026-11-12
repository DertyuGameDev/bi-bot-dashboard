"""Region facets and every chart must use the same applied filter selection."""

import pytest
from conftest import build, post


def test_outlet_choices_follow_single_multiple_and_cleared_regions(client):
    initial = build(client)
    state = client.app.state.store.load("web:" + client.cookies["puzzle_session"])
    rows = state["dataset"]["rows"]
    regions = initial["options"]["regions"]
    for selection in [[regions[0]], regions[:2], []]:
        result = post(client, "/api/filters", {"regions": selection}).json()
        expected = {row["outlet"] for row in rows if not selection or row["region"] in selection}
        assert set(result["options"]["outlets"]) == expected
        assert result["messages"][-1]["dashboard"]["filters"] == result["filters"]
        assert result["messages"][-1]["dashboard"]["kpis"] == result["kpis"]


def test_combined_filters_recompute_all_four_charts_from_the_same_rows(client):
    initial = build(client)
    state = client.app.state.store.load("web:" + client.cookies["puzzle_session"])
    row = next(row for row in state["dataset"]["rows"] if row["date"] == "2025-02-01")
    result = post(
        client,
        "/api/filters",
        {
            "regions": [row["region"]],
            "outlets": [row["outlet"]],
            "products": [row["product"]],
            "start": "2025-02-01",
            "end": "2025-02-28",
            "dimension": "outlet",
        },
    ).json()
    charts = result["messages"][-1]["dashboard"]["charts"]
    assert len(charts) == 4 and all(chart["status"] == "ready" and chart["svg"] for chart in charts)
    assert [chart["id"] for chart in charts] == [chart["id"] for chart in initial["charts"]]
    for group in [charts, result["charts"]]:
        for chart in group[:3]:
            points = [point for point in chart["metadata"]["tips"].values() if point["series"] == "fact"]
            assert points and sum(point["value"] for point in points) == pytest.approx(row["fact"])
        completion = list(group[3]["metadata"]["tips"].values())
        assert len(completion) == 1
        assert completion[0]["value"] == pytest.approx(row["fact"] / row["plan"] * 100)
    assert result["kpis"]["fact"] == pytest.approx(row["fact"])
    assert result["debug"]["model"] == initial["debug"]["model"]


def test_empty_region_clears_every_chart_then_reset_restores_every_chart(client):
    initial = build(client)
    empty = post(client, "/api/filters", {"regions": ["Регион без данных"]}).json()
    assert empty["options"]["outlets"] == []
    assert empty["dataset"]["rows"] == 0
    assert all(
        chart["status"] == "empty" and not chart["svg"]
        for chart in empty["messages"][-1]["dashboard"]["charts"]
    )
    restored = post(client, "/api/filters", {"regions": []}).json()
    assert restored["kpis"] == initial["kpis"]
    assert restored["options"]["outlets"] == initial["options"]["outlets"]
    assert all(
        chart["status"] == "ready" and chart["svg"]
        for chart in restored["messages"][-1]["dashboard"]["charts"]
    )
