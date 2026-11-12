import pandas as pd
import pytest
from shared.source import Source, SourceError, flatten
from shared.config import Settings


def frames():
    return {
        "regions": pd.DataFrame({"region_id": [1], "region_name": ["Центральный"]}),
        "outlets": pd.DataFrame({"outlet_id": [1], "outlet_name": ["Точка"], "region_id": [1]}),
        "products": pd.DataFrame(
            {"product_id": [1], "product_name": ["Продукт"], "category": ["Категория"], "unit": ["шт"]}
        ),
        "clients": pd.DataFrame({"client_id": [1], "client_name": ["Клиент"]}),
        "sales": pd.DataFrame(
            {
                "sale_id": [1, 2],
                "sale_date": ["2025-01-02", "2025-01-03"],
                "client_id": [1, 1],
                "outlet_id": [1, 1],
                "product_id": [1, 1],
                "amount": [30.0, 40.0],
                "quantity": [3.0, 4.0],
            }
        ),
        "sales_plan": pd.DataFrame(
            {
                "plan_id": [1],
                "plan_month": ["2025-01-01"],
                "outlet_id": [1],
                "product_id": [1],
                "plan_amount": [100.0],
                "plan_qty": [10.0],
            }
        ),
        "discounts": pd.DataFrame(
            {
                "discount_id": [1, 2],
                "sale_id": [1, 1],
                "client_id": [1, 1],
                "discount_date": ["2025-01-02"] * 2,
                "discount_amount": [2.0, 3.0],
                "discount_pct": [1.0, 2.0],
                "reason": ["A", "B"],
            }
        ),
    }


def test_raw_one_to_many_does_not_multiply_plan_or_fact():
    table, _, quality = flatten(frames())
    assert len(table) == 1
    assert table.plan.sum() == 100
    assert table.fact.sum() == 70
    assert table.discount.sum() == 5
    assert quality["raw_fact_sum"] == quality["flat_fact_sum"]


def test_duplicate_keys_stop_instead_of_deduplicating():
    f = frames()
    f["sales"] = pd.concat([f["sales"], f["sales"].head(1)])
    with pytest.raises(SourceError, match="ключ"):
        flatten(f)


def test_duplicate_plan_grain_rejected():
    f = frames()
    f["sales_plan"] = pd.concat([f["sales_plan"], f["sales_plan"].assign(plan_id=2)])
    with pytest.raises(SourceError, match="План содержит повтор"):
        flatten(f)


def test_discount_client_detail_never_allocates_plan():
    table, _, _ = flatten(frames(), "discounts")
    assert table.plan.isna().all()
    assert table.fact.sum() == 30
    assert table.discount.sum() == 5


def test_database_failure_redacts_dsn():
    config = Settings(
        database_url="postgresql://secret_user:secret_password@127.0.0.1:1/missing", _env_file=None
    )
    with pytest.raises(SourceError) as e:
        Source(config).schema()
    assert "secret_password" not in str(e.value)
    assert "secret_user" not in str(e.value)
