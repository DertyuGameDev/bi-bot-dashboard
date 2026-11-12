"""KPI cards share filter scope, count real activity and compare equivalent ranges."""

import copy
import pandas as pd
import pytest
from conftest import build, post
from shared.analytics import empty_state, install_dataset, dashboard_kpis, dataset_frame
from shared.client_activity import encode_activity, verified_activity
from shared.kpi_summary import activity_counts, comparison_window
from scripts.backfill_activity import enrich_state


@pytest.fixture
def state():
    rows = []
    for year, factor in [(2024, 1), (2025, 2)]:
        for month in range(1, 13):
            for outlet in [1, 2, 3]:
                # Third outlet/product is planned but has never sold anything.
                rows.append(
                    dict(
                        date=f"{year}-{month:02}-01",
                        region="A" if outlet != 2 else "B",
                        outlet=f"Point {outlet}",
                        outlet_id=outlet,
                        product=f"Product {outlet}",
                        product_id=outlet,
                        client="Не распределён",
                        category="C",
                        unit="шт",
                        fact=float(month * factor if outlet < 3 else 0),
                        plan=30.0,
                        deals=1.0 if outlet < 3 else 0.0,
                        quantity=1.0,
                        plan_quantity=2.0,
                        discount=float(factor),
                    )
                )
    sales = pd.DataFrame(
        [
            dict(
                sale_date=r["date"],
                outlet_id=r["outlet_id"],
                product_id=r["product_id"],
                client_id=month % 2 + 1,
                amount=r["fact"],
            )
            for month, r in enumerate(rows)
            if r["deals"]
        ]
    )
    result = empty_state()
    install_dataset(
        result,
        dict(
            rows=rows,
            columns=list(rows[0]),
            kind="sales",
            grain="month × outlet × product",
            source="Fixture",
            query_start="2024-01-01",
            query_end="2025-12-31",
            observed_end="2025-12-31",
            client_activity=encode_activity(sales),
        ),
    )
    result["filters"].update(start="2025-01-01", end="2025-12-31")
    return result


def test_plan_fact_deviation_activity_and_full_previous_year(state):
    values = dashboard_kpis(state)
    assert values["plan"] == 1080 and values["fact"] == 312
    assert values["completion"] == pytest.approx(312 / 1080 * 100)
    assert values["deviation"] == -768
    assert values["deviation_pct"] == pytest.approx(312 / 1080 * 100 - 100)
    assert (values["active_clients"], values["active_outlets"], values["active_products"]) == (2, 2, 2)
    assert values["outlets"] == 3  # plan-only outlet must not inflate activity
    assert values["growth"] == 100 and values["previous_fact"] == 156
    assert values["comparison_period"]["start"] == "2024-01-01"
    assert values["comparison_period"]["end"] == "2024-12-31"


def test_filters_apply_to_every_card_and_top_n_does_not_limit_kpi(state):
    state["filters"].update(
        regions=["A"], products=["Product 1"], start="2025-02-01", end="2025-02-28", top_n=1
    )
    values = dashboard_kpis(state)
    assert values["fact"] == 4 and values["plan"] == 30
    assert values["active_clients"] == values["active_outlets"] == values["active_products"] == 1
    assert values["previous_fact"] == 2 and values["growth"] == 100
    state["filters"].update(regions=[], products=[], start="2025-01-01", end="2025-12-31")
    assert dashboard_kpis(state)["active_outlets"] == 2
    state["filters"]["outlets"] = ["Point 3"]
    plan_only = dashboard_kpis(state)
    assert plan_only["active_clients"] == plan_only["active_outlets"] == plan_only["active_products"] == 0
    assert plan_only["growth"] is None and "нулевой" in plan_only["growth_notice"]


def test_zero_revenue_sales_are_activity_and_ids_avoid_duplicate_names():
    frame = pd.DataFrame(
        dict(
            fact=[0, 100, 0],
            deals=[1, 2, 0],
            client_id=[7, 8, None],
            outlet=["Same"] * 3,
            outlet_id=[1, 2, 3],
            product=["Same"] * 3,
            product_id=[1, 2, 3],
        )
    )
    assert activity_counts(frame) == dict(active_clients=2, active_outlets=2, active_products=2)
    frame.loc[0, "client_id"] = None
    assert activity_counts(frame)["active_clients"] is None


def test_unknown_client_placeholder_is_not_one_and_empty_is_zero(state):
    state["dataset"].pop("client_activity")
    assert dashboard_kpis(state)["active_clients"] is None
    state["filters"]["regions"] = ["Unknown"]
    values = dashboard_kpis(state)
    assert values["active_clients"] == values["active_outlets"] == values["active_products"] == 0
    assert values["growth"] is None and "Нет фактических" in values["growth_notice"]


@pytest.mark.parametrize(
    "metric,actual,previous,growth",
    [
        ("discount", 72, 36, 100),
        ("deals", 24, 24, 0),
        ("completion", 312 / 1080 * 100, 156 / 1080 * 100, 156 / 1080 * 100),
    ],
)
def test_metric_units_and_completion_cards_keep_financial_totals(state, metric, actual, previous, growth):
    state["filters"]["metric"] = metric
    values = dashboard_kpis(state)
    assert values["fact"] == pytest.approx(actual)
    assert values["previous_fact"] == pytest.approx(previous)
    assert values["growth"] == pytest.approx(growth)
    if metric == "completion":
        assert values["totals"] == dict(
            metric="amount",
            plan=1080,
            fact=312,
            deviation=-768,
            deviation_pct=pytest.approx(312 / 1080 * 100 - 100),
        )
        assert values["growth_unit"] == "points"
    else:
        assert values["plan"] is None and values["completion"] is None
        assert values["growth_unit"] == "percent"


def test_year_to_date_compares_same_elapsed_part_and_full_year_plan(state):
    state["dataset"]["observed_end"] = "2025-09-30"
    for row in state["dataset"]["rows"]:
        if row["date"] > "2025-09-30":
            row.update(fact=0.0, deals=0.0)
    values = dashboard_kpis(state)
    assert values["fact"] == 180 and values["previous_fact"] == 90 and values["growth"] == 100
    assert values["comparison_period"]["end"] == "2024-09-30"
    assert values["comparison_period"]["full_end"] == "2024-12-31"
    state["filters"]["metric"] = "completion"
    ratio = dashboard_kpis(state)
    assert ratio["fact"] == pytest.approx(180 / 1080 * 100)
    assert ratio["previous_fact"] == pytest.approx(90 / 1080 * 100)
    state["dataset"]["observed_end"] = "2025-09-17"
    assert "Месяц ещё не завершён" in dashboard_kpis(state)["growth_notice"]


def test_absent_or_zero_baseline_never_becomes_misleading_growth(state):
    state["dataset"]["query_start"] = "2025-01-01"
    assert "нет полной базы" in dashboard_kpis(state)["growth_notice"]
    state["dataset"]["query_start"] = "2024-01-01"
    for row in state["dataset"]["rows"]:
        if row["date"] < "2025-01-01":
            row["fact"] = 0.0
    values = dashboard_kpis(state)
    assert values["growth"] is None and "нулевой" in values["growth_notice"]


def test_calendar_quarter_and_daily_equal_length_windows(state):
    state["filters"].update(start="2025-04-01", end="2025-06-30", granularity="quarter")
    values = dashboard_kpis(state)
    assert values["comparison_period"]["start"] == "2025-01-01"
    assert values["previous_fact"] == 24 and values["fact"] == 60 and values["growth"] == 150
    from shared.models import Filters

    window = comparison_window(
        dict(kind="discounts", query_start="2025-01-01", query_end="2025-03-31"),
        Filters(start="2025-03-12", end="2025-03-18"),
    )
    assert window[3] == pd.Timestamp("2025-03-05") and window[4] == pd.Timestamp("2025-03-11")

    window = comparison_window(
        dict(kind="sales", query_start="2024-01-01", query_end="2025-12-31", observed_end="2025-02-28"),
        Filters(start="2025-01-01", end="2025-03-31"),
    )
    assert window[3] == pd.Timestamp("2024-10-01") and window[4] == pd.Timestamp("2024-11-30")


def test_verified_legacy_backfill_is_idempotent_and_preserves_frozen_data(state):
    state["dataset"].pop("client_activity")
    before = copy.deepcopy(state)
    sales = pd.DataFrame(
        [
            dict(
                sale_date=r["date"],
                outlet_id=r["outlet_id"],
                product_id=r["product_id"],
                client_id=1,
                amount=r["fact"],
            )
            for r in state["dataset"]["rows"]
            if r["deals"]
        ]
    )

    class Source:
        def load_records(self, *args):
            return dict(columns=list(sales), rows=sales.values.tolist())

    assert enrich_state(state, Source(), {})
    assert not enrich_state(state, Source(), {})
    assert state["dataset"]["rows"] == before["dataset"]["rows"]
    assert state["dataset"]["id"] == before["dataset"]["id"]
    assert dashboard_kpis(state)["active_clients"] == 1
    sales.loc[0, "amount"] += 1
    with pytest.raises(ValueError, match="Источник изменился"):
        verified_activity(state["dataset"], sales)
    sales.loc[0, "amount"] -= 1
    sales = pd.concat([sales, sales.iloc[[0]].assign(amount=0)])
    with pytest.raises(ValueError, match="Источник изменился"):
        verified_activity(state["dataset"], sales)


def test_all_cards_persist_in_snapshots_and_restore_without_model_or_source(client, monkeypatch):
    initial = build(client)
    from shared.source import Source

    def forbidden(*args, **kwargs):
        raise AssertionError("Reopening must use frozen KPI data")

    monkeypatch.setattr(Source, "load", forbidden)
    monkeypatch.setattr(client.app.state.chat_service.agent, "run", forbidden)
    post(client, "/api/dashboards/save", {"title": "Рабочая копия", "as_new": True})
    post(client, "/api/filters", {"regions": ["Центральный"]})
    restored = post(client, f"/api/dashboards/{initial['saved_id']}/open", {}).json()
    assert restored["kpis"] == initial["kpis"]
    assert restored["messages"][-1]["dashboard"]["kpis"] == initial["kpis"]
    assert dataset_frame(client.app.state.store.load("web:" + client.cookies["puzzle_session"])).shape[0] > 0
