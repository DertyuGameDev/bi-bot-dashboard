import numpy as np
import pandas as pd
import pytest
from conftest import build, post
from shared.analytics import chart_frame, kpis
from shared.calculations import aggregate
from shared.models import ChartSpec, Filters


def test_year_completion_dynamics_excludes_unobserved_future_plans(observations):
    frame = observations.copy()
    frame.loc[frame.date == "2025-04-01", "fact"] = 0.0
    frame.attrs["observed_end"] = "2025-03-31"
    out, _ = chart_frame(
        frame,
        ChartSpec(source="period", y=["fact"]),
        Filters(
            metric="completion", granularity="year", value_mode="dynamics", end="2025-12-31"
        ).model_dump(),
    )
    assert out.loc[out.period == "2025", "fact"].iloc[0] == pytest.approx(-2)


@pytest.fixture
def observations():
    rows = []
    for date, a, b, pa, pb, da, db in [
        ("2024-12-01", 80, 20, 100, 100, 2, 1),
        ("2025-01-01", 120, 30, 100, 300, 3, 2),
        ("2025-02-01", 60, 90, 200, 100, 1, 4),
        ("2025-03-01", 180, 0, 200, 100, 5, 0),
        ("2025-04-01", 0, 90, 100, 100, 0, 2),
    ]:
        for region, fact, plan, deals, unit in [("A", a, pa, da, "кг"), ("B", b, pb, db, "шт")]:
            rows.append(
                dict(
                    date=date,
                    region=region,
                    outlet=region,
                    product=region,
                    category=region,
                    client="",
                    unit=unit,
                    fact=float(fact),
                    plan=float(plan),
                    deals=float(deals),
                    discount=fact / 10,
                    quantity=deals * 2.0,
                    plan_quantity=20.0,
                )
            )
    return pd.DataFrame(rows)


@pytest.mark.parametrize(
    "metric,actual,plan",
    [("amount", 570, 1200), ("discount", 57, None), ("deals", 17, None), ("completion", 47.5, 100)],
)
def test_metric_totals_use_the_correct_units_and_do_not_invent_plans(observations, metric, actual, plan):
    df = observations[observations.date >= "2025-01-01"]
    values = kpis(df, Filters(metric=metric).model_dump())
    assert values["fact"] == pytest.approx(actual)
    assert values["plan"] == plan
    grouped = aggregate(df, "region", metric)
    if metric != "completion":
        assert grouped.fact.sum() == pytest.approx(actual)


def test_completion_is_weighted_ratio_and_zero_plan_is_unknown(observations):
    january = observations[observations.date == "2025-01-01"]
    values = kpis(january, Filters(metric="completion").model_dump())
    assert values["fact"] == 37.5
    assert values["fact"] != 65  # mean of 120% and 10% is not total completion
    january = january.copy()
    january["plan"] = 0.0
    assert kpis(january, Filters(metric="completion").model_dump())["fact"] is None
    assert aggregate(january, "region", "completion").fact.isna().all()


def test_shares_use_all_selected_groups_before_top_n(observations):
    df = observations[observations.date >= "2025-01-01"]
    out, spec = chart_frame(
        df,
        ChartSpec(source="ranking", x="region", y=["plan", "fact"]),
        Filters(value_mode="percent", top_n=1).model_dump(),
    )
    assert len(out) == 1 and out.region.iloc[0] == "A"
    assert out.fact.iloc[0] == pytest.approx(360 / 570 * 100)
    assert out.plan.iloc[0] == 50
    assert spec.number_format == "percent" and spec.y == ["plan", "fact"]


@pytest.mark.parametrize(
    "granularity,labels,values",
    [
        ("month", ["2025-01", "2025-02", "2025-03", "2025-04"], [150, 150, 180, 90]),
        ("quarter", ["2025Q1", "2025Q2"], [480, 90]),
        ("year", ["2025"], [570]),
    ],
)
def test_period_buckets_sum_original_values(observations, granularity, labels, values):
    df = observations[observations.date >= "2025-01-01"]
    out, _ = chart_frame(df, ChartSpec(source="period"), Filters(granularity=granularity).model_dump())
    assert out.period.tolist() == labels and out.fact.tolist() == values


def test_dynamics_uses_prior_calendar_period_and_keeps_loaded_baseline(observations):
    visible = observations[observations.date >= "2025-01-01"]
    filters = Filters(start="2025-01-01", end="2025-04-30", value_mode="dynamics").model_dump()
    out, spec = chart_frame(visible, ChartSpec(source="period", y=["fact"]), filters, comparison=observations)
    assert out.period.tolist() == ["2025-01", "2025-02", "2025-03", "2025-04"]
    assert out.fact.tolist() == pytest.approx([50, 0, 20, -50])
    assert spec.number_format == "percent"
    missing = observations[observations.date != "2025-02-01"]
    out, _ = chart_frame(missing, ChartSpec(source="period", y=["fact"]), filters)
    assert np.isnan(out.loc[out.period == "2025-03", "fact"].iloc[0])


def test_categorical_and_heatmap_dynamics_compare_each_entity_over_time(observations):
    filters = Filters(start="2025-01-01", end="2025-04-30", value_mode="dynamics").model_dump()
    out, spec = chart_frame(observations, ChartSpec(source="ranking", x="region", y=["fact"]), filters)
    assert out.loc[out.region == "A", "fact"].iloc[0] == -100
    assert np.isnan(out.loc[out.region == "B", "fact"].iloc[0])  # prior value was zero
    heat, _ = chart_frame(
        observations,
        ChartSpec(type="heatmap", source="table", x="period", group="region", y=["fact"]),
        filters,
    )
    assert heat.loc[(heat.region == "B") & (heat.period == "2025-02"), "fact"].iloc[0] == 200


def test_completion_dynamics_is_percentage_points(observations):
    out, spec = chart_frame(
        observations,
        ChartSpec(source="period", y=["fact"]),
        Filters(
            metric="completion", value_mode="dynamics", start="2024-12-01", end="2025-04-30"
        ).model_dump(),
    )
    assert out.loc[out.period == "2025-01", "fact"].iloc[0] == -12.5
    assert out.loc[out.period == "2025-04", "fact"].iloc[0] == -15
    assert spec.number_format == "points"


@pytest.mark.parametrize("metric", ["amount", "discount", "deals", "completion"])
def test_switches_render_and_survive_snapshot_restore_without_the_model(client, metric):
    initial = build(client)
    out = post(
        client,
        "/api/filters",
        {"metric": metric, "granularity": "quarter", "value_mode": "percent", "dimension": "outlet"},
    ).json()
    assert out["filters"]["metric"] == metric and len(out["messages"][-1]["dashboard"]["charts"]) == 4
    assert all(
        c["status"] == "ready" and c["spec"]["number_format"] == "percent"
        for c in out["messages"][-1]["dashboard"]["charts"]
    )
    saved = post(client, "/api/dashboards/save", {"title": "Режим " + metric}).json()
    post(client, "/api/dashboards/save", {"title": "Рабочая копия", "as_new": True})
    post(client, "/api/filters", {"metric": "amount", "value_mode": "absolute", "granularity": "month"})
    restored = post(client, f"/api/dashboards/{saved['saved_id']}/open", {}).json()
    assert restored["filters"] == saved["filters"] and restored["kpis"] == saved["kpis"]
    assert restored["debug"]["model"] == initial["debug"]["model"]


def test_year_dynamics_can_use_preceding_loaded_year(client):
    build(client)
    response = post(client, "/api/filters", {"granularity": "year", "value_mode": "dynamics"})
    assert response.status_code == 200, response.text
    assert all(c["status"] == "ready" for c in response.json()["messages"][-1]["dashboard"]["charts"])


def test_existing_comparison_switches_to_plan_fact_by_the_selected_dimension(client):
    build(client)
    store = client.app.state.store
    owner = "web:" + client.cookies["puzzle_session"]
    state = store.load(owner)
    state["dashboard"]["charts"][2].update(id="existing-outlet-revenue", y=["fact"])
    store.commit(owner, state, state["revision"])
    out = post(client, "/api/filters", {"dimension": "outlet"}).json()
    chart = out["messages"][-1]["dashboard"]["charts"][2]
    assert chart["spec"]["x"] == "outlet" and chart["spec"]["y"] == ["plan", "fact"]


def test_missing_previous_year_remains_unknown_instead_of_zero(client):
    build(client)
    store = client.app.state.store
    owner = "web:" + client.cookies["puzzle_session"]
    state = store.load(owner)
    state["dataset"]["query_start"] = "2025-01-01"
    state["dataset"]["rows"] = [row for row in state["dataset"]["rows"] if row["date"] >= "2025-01-01"]
    store.commit(owner, state, state["revision"])
    response = post(client, "/api/filters", {"granularity": "year", "value_mode": "dynamics"})
    assert response.status_code == 200, response.text
    charts = response.json()["messages"][-1]["dashboard"]["charts"]
    assert all(c["status"] == "empty" and "предыдущего" in c["notice"] for c in charts)


def test_empty_filter_in_dynamics_is_a_valid_empty_result(client):
    build(client)
    response = post(
        client, "/api/filters", {"regions": ["Нет региона"], "value_mode": "dynamics", "metric": "completion"}
    )
    assert response.status_code == 200, response.text
    assert all(c["status"] == "empty" for c in response.json()["messages"][-1]["dashboard"]["charts"])


def test_dynamics_does_not_compare_future_plan_only_months(observations):
    observations.attrs["observed_end"] = "2025-03-31"
    out, _ = chart_frame(
        observations,
        ChartSpec(source="ranking", x="region", y=["fact"]),
        Filters(value_mode="dynamics", end="2025-12-31").model_dump(),
    )
    assert out.loc[out.region == "A", "fact"].iloc[0] == 200
    assert out.loc[out.region == "B", "fact"].iloc[0] == -100


def test_dimension_switch_updates_heatmap_rows(client):
    build(client)
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
    out = post(client, "/api/filters", {"dimension": "region"}).json()
    chart = out["messages"][-1]["dashboard"]["charts"][0]
    assert chart["spec"]["group"] == "region" and chart["spec"]["x"] == "period"
    assert all(
        any(region in tip["label"] for region in out["options"]["regions"])
        for tip in chart["metadata"]["tips"].values()
    )


def test_categorical_table_chart_aggregates_the_selected_dimension(client):
    build(client)
    post(
        client,
        "/api/chart",
        {
            "index": 1,
            "changes": {"type": "grouped_bar", "source": "table", "x": "outlet", "y": ["plan", "fact"]},
        },
    )
    out = post(client, "/api/filters", {"dimension": "region"}).json()
    chart = out["messages"][-1]["dashboard"]["charts"][0]
    assert chart["spec"]["x"] == "region"
    points = list(chart["metadata"]["tips"].values())
    assert len(points) == 2 * len(out["options"]["regions"])
    assert sum(t["value"] for t in points if t["series"] == "fact") == pytest.approx(out["kpis"]["fact"])


def test_table_comparison_recomputes_formula_after_grouping(observations):
    recipe = {
        "name": "custom_ratio",
        "label": "Выполнение",
        "expression": "fact/plan*100",
        "kind": "formula",
        "dependencies": ["fact", "plan"],
    }
    frame = observations[observations.date == "2025-01-01"].copy()
    frame["region"] = "A"
    frame["custom_ratio"] = frame.fact / frame.plan * 100
    out, _ = chart_frame(
        frame, ChartSpec(source="table", x="region", y=["custom_ratio"]), Filters().model_dump(), [recipe]
    )
    assert len(out) == 1 and out.custom_ratio.iloc[0] == 37.5


def test_dynamics_keeps_current_date_filters_while_using_complete_prior_bucket(observations):
    visible = observations[observations.date >= "2025-02-01"]
    out, _ = chart_frame(
        visible,
        ChartSpec(source="period", y=["fact"]),
        Filters(
            value_mode="dynamics", granularity="quarter", start="2025-02-01", end="2025-04-30"
        ).model_dump(),
        comparison=observations,
    )
    # Selected Q1 is February+March (330), previous Q4 is 100.
    assert out.loc[out.period == "2025Q1", "fact"].iloc[0] == pytest.approx(230)
    # Q2 compares the selected April (90) with complete cached Q1 (480).
    assert out.loc[out.period == "2025Q2", "fact"].iloc[0] == pytest.approx(-81.25)


def test_global_view_uses_selected_metric_without_destroying_custom_chart_recipe(observations):
    spec = ChartSpec(source="period", y=["custom_ratio"])
    recipe = {
        "name": "custom_ratio",
        "label": "Выполнение",
        "expression": "fact/plan*100",
        "kind": "formula",
        "dependencies": ["fact", "plan"],
    }
    out, effective = chart_frame(
        observations, spec, Filters(value_mode="dynamics", end="2025-04-30").model_dump(), [recipe]
    )
    assert effective.y == ["fact"] and out.loc[out.period == "2025-02", "fact"].iloc[0] == 0
    assert spec.y == ["custom_ratio"]
    absolute, restored = chart_frame(observations, spec, Filters().model_dump(), [recipe])
    assert (
        restored.y == ["custom_ratio"]
        and absolute.loc[absolute.period == "2025-01", "custom_ratio"].iloc[0] == 37.5
    )


def test_calculated_series_keep_their_user_label_on_axes_legends_and_tooltips(observations):
    from shared.charts import render
    import xml.etree.ElementTree as ET

    spec = ChartSpec(source="period", y=["gap"], xlabel="period", ylabel="gap")
    recipe = {
        "name": "gap",
        "label": "Отклонение от плана",
        "expression": "fact-plan",
        "kind": "formula",
        "dependencies": ["fact", "plan"],
    }
    table, effective = chart_frame(observations, spec, Filters().model_dump(), [recipe])
    result = render(table, effective)
    assert result["metadata"]["series"][0]["label"] == recipe["label"]
    assert all(recipe["label"] in tip["text"] for tip in result["metadata"]["tips"].values())
    texts = [e.text for e in ET.fromstring(result["svg"]).iter() if e.tag.endswith("}text")]
    assert "Период" in texts and recipe["label"] in texts and "gap" not in texts
