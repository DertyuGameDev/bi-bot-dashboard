import pandas as pd
import pytest
from shared.analytics import empty_state
from shared.storage import Store
from shared.tools import Tools
from shared.source import Source, SourceError
from shared.config import Settings


def toolset(settings):
    return Tools(settings, Store(settings.storage), "qa", empty_state())


def test_generic_records_find_first_client_purchase_over_entire_history(settings):
    tools = toolset(settings)
    frame = pd.DataFrame(
        [
            {"client_id": 1, "sale_date": "2024-12-30", "amount": 10},
            {"client_id": 1, "sale_date": "2025-07-01", "amount": 20},
            {"client_id": 2, "sale_date": "2025-06-08", "amount": 30},
            {"client_id": 3, "sale_date": "2025-07-04", "amount": 40},
            {"client_id": 3, "sale_date": "2025-07-08", "amount": 50},
        ]
    )
    tools.put_records("sales", {"rows": frame.to_dict("records"), "columns": frame.columns.tolist()})
    tools.invoke(
        "analyze_records",
        {
            "source": "sales",
            "name": "firsts",
            "group_by": ["client_id"],
            "measures": [{"name": "first_sale", "operation": "min", "column": "sale_date"}],
        },
    )
    out = tools.invoke(
        "analyze_records",
        {
            "source": "firsts",
            "date_column": "first_sale",
            "bucket": "month",
            "conditions": [
                {"column": "first_sale", "operator": "gte", "value": "2025-06-01"},
                {"column": "first_sale", "operator": "lte", "value": "2025-07-31"},
            ],
            "measures": [{"name": "new_clients", "operation": "count"}],
            "expressions": {"per_day": "new_clients / calendar_days"},
        },
    )
    assert [row["new_clients"] for row in out["rows"]] == [1, 1]
    assert out["rows"][0]["per_day"] == pytest.approx(1 / 30)
    assert out["rows"][1]["per_day"] == pytest.approx(1 / 31)


def test_calendar_includes_days_with_zero_contracts_in_daily_mean(settings):
    tools = toolset(settings)
    tools.put_records(
        "contracts",
        {
            "rows": [
                {"id": 1, "start_date": "2025-07-01"},
                {"id": 2, "start_date": "2025-07-01"},
                {"id": 3, "start_date": "2025-07-03"},
            ],
            "columns": ["id", "start_date"],
        },
    )
    out = tools.invoke(
        "analyze_records",
        {
            "source": "contracts",
            "date_column": "start_date",
            "bucket": "day",
            "measures": [{"name": "contracts", "operation": "count"}],
            "calendar_start": "2025-07-01",
            "calendar_end": "2025-07-03",
        },
    )
    assert [row["contracts"] for row in out["rows"]] == [2, 0, 1]
    assert out["statistics"]["contracts"]["mean"] == 1


def test_record_join_enforces_relationship_cardinality(settings):
    tools = toolset(settings)
    tools.put_records("left", {"rows": [{"id": 1}, {"id": 1}], "columns": ["id"]})
    tools.put_records("right", {"rows": [{"id": 1}, {"id": 1}], "columns": ["id"]})
    with pytest.raises(ValueError, match="not unique"):
        tools.invoke(
            "join_records",
            {"left": "left", "right": "right", "name": "joined", "left_on": ["id"], "right_on": ["id"]},
        )


def test_source_records_rejects_sql_as_table_name(settings):
    with pytest.raises(SourceError, match="Неизвестная"):
        Source(settings).load_records("clients;DROP TABLE sales", [], [])


@pytest.mark.integration
def test_real_clients_and_contracts_and_parameterized_filters():
    import os

    if os.getenv("RPA_TEST_POSTGRES") != "1":
        pytest.skip("Requires imported Puzzle database")
    source = Source(Settings())
    clients = source.load_records("clients", ["client_id", "registered_date"], [])
    contracts = source.load_records("contracts", ["contract_id", "start_date"], [])
    assert len(clients["rows"]) == 180 and len(contracts["rows"]) == 617
    selected = source.load_records(
        "contracts",
        ["contract_id", "start_date"],
        [
            {"column": "start_date", "operator": "gte", "value": "2025-06-01"},
            {"column": "start_date", "operator": "lte", "value": "2025-07-31"},
        ],
    )
    expected = [r for r in contracts["rows"] if "2025-06-01" <= r["start_date"] <= "2025-07-31"]
    assert selected["rows"] == expected and len(expected) > 0
    assert (
        source.load_records(
            "clients", ["client_id"], [{"column": "client_name", "operator": "eq", "value": "' OR 1=1 --"}]
        )["rows"]
        == []
    )
    with pytest.raises(SourceError):
        source.load_records("clients", ["bad_field"], [])
    with pytest.raises(SourceError, match="лимит"):
        Source(Settings(row_limit=1)).load_records("clients", [], [])
