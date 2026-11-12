"""Integration checks against the actual imported case archive, opt-in for other machines."""

import os
import pytest
from shared.config import Settings
from shared.source import Source, SourceError
from shared.analytics import empty_state, install_dataset, present, default_charts

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RPA_TEST_POSTGRES") != "1",
        reason="Set RPA_TEST_POSTGRES=1 after importing the original archive",
    ),
]


@pytest.fixture(scope="module")
def actual_data():
    return Source(Settings()).load("2024-01-01", "2025-12-31")


def test_actual_schema_not_invented():
    schema = Source(Settings()).schema()
    fields = {(x["table_name"], x["column_name"]) for x in schema["tables"]}
    assert {("sales", "sale_id"), ("sales_plan", "plan_month"), ("discounts", "sale_id")} <= fields
    assert len(schema["relations"]) >= 7


def test_actual_source_sums_and_reference_year(actual_data):
    quality = actual_data["quality"]
    assert quality["raw_fact_sum"] == pytest.approx(quality["flat_fact_sum"])
    assert quality["raw_plan_sum"] == pytest.approx(quality["flat_plan_sum"])
    assert quality["duplicate_plan_grain"] == 0
    state = empty_state()
    install_dataset(state, actual_data)
    state["filters"].update(start="2025-01-01", end="2025-12-31")
    state["dashboard"]["charts"] = default_charts()
    result = present(state)
    assert result["kpis"]["fact"] == pytest.approx(1002960930.71)
    assert result["kpis"]["plan"] == pytest.approx(1105705900.0)
    assert len(result["charts"]) == 4


def test_actual_reader_cannot_write():
    source = Source(Settings())
    with pytest.raises(SourceError, match="ReadOnlySqlTransaction"):
        with source.connect() as c:
            c.execute("UPDATE sales SET amount=amount WHERE false")


def test_real_kpi_activity_and_growth_match_original_sales(actual_data):
    import pandas as pd
    from shared.analytics import dashboard_kpis

    raw = Source(Settings()).load_records(
        "sales",
        ["sale_date", "outlet_id", "product_id", "client_id", "amount"],
        [
            {"column": "sale_date", "operator": "gte", "value": "2024-01-01"},
            {"column": "sale_date", "operator": "lte", "value": "2025-12-31"},
        ],
    )
    sales = pd.DataFrame(raw["rows"], columns=raw["columns"])
    state = empty_state()
    install_dataset(state, actual_data)
    state["filters"].update(start="2025-01-01", end="2025-12-31")
    current = sales.loc[sales.sale_date >= "2025-01-01"]
    previous = sales.loc[sales.sale_date < "2025-01-01"]
    values = dashboard_kpis(state)
    assert values["active_clients"] == current.client_id.nunique()
    assert values["active_outlets"] == current.outlet_id.nunique()
    assert values["active_products"] == current.product_id.nunique()
    assert values["growth"] == pytest.approx((current.amount.sum() / previous.amount.sum() - 1) * 100)
    assert values["previous_fact"] == pytest.approx(previous.amount.sum())
    # Shared region/outlet/product filters must restrict client membership as well.
    cell = next(r for r in actual_data["rows"] if r["deals"] and r["date"].startswith("2025"))
    state["filters"].update(regions=[cell["region"]], outlets=[cell["outlet"]], products=[cell["product"]])
    selected = current.loc[
        (current.outlet_id == cell["outlet_id"]) & (current.product_id == cell["product_id"])
    ]
    scoped = dashboard_kpis(state)
    assert scoped["active_clients"] == selected.client_id.nunique()
    assert scoped["fact"] == pytest.approx(selected.amount.sum())


def test_actual_limit_does_not_return_partial_data():
    config = Settings(row_limit=1)
    with pytest.raises(SourceError, match="лимит"):
        Source(config).load("2025-01-01", "2025-12-31")


def test_all_history_and_requested_year_feed_real_interactive_charts(tmp_path):
    from shared.analytics import materialize, kpis
    from shared.storage import Store
    from shared.tools import Tools
    from shared.chat import ChatService

    settings = Settings(storage=str(tmp_path / "snapshots.sqlite3"))
    store = Store(settings.storage)
    state = empty_state()
    tools = Tools(settings, store, "qa", state)
    all_time = tools.invoke("fetch_data", {"all_time": True})
    assert all_time["period"] == ["2023-01-01", "2026-09-30"]
    assert state["filters"]["start"] == "2023-01-01"
    assert state["filters"]["end"] == "2026-09-30"
    assert kpis(materialize(state)[0], state["filters"])["fact"] == pytest.approx(3377320610.28)
    tools.invoke("fetch_data", {"start": "2026-01-01", "end": "2026-12-31"})
    tools.invoke("apply_filters", {"filters": {"dimension": "outlet"}})
    tools.invoke(
        "create_dashboard",
        {
            "charts": [
                {"id": "outlets", "type": "horizontal_bar", "source": "ranking", "x": "outlet", "y": ["fact"]}
            ]
        },
    )
    message = {}
    ChatService(settings, store).dashboard("qa", state, message)
    dashboard = message["dashboard"]
    assert dashboard["kpis"]["fact"] == pytest.approx(840527871.13)
    assert "2026-09-30" in dashboard["coverage_notice"]
    assert dashboard["filters"]["start"] == "2026-01-01"
    assert dashboard["charts"][0]["status"] == "ready"
    assert len(dashboard["charts"][0]["metadata"]["tips"]) == 10
