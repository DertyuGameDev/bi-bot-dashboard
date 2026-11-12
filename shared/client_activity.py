"""Frozen client membership for monthly sales; no allocation of plans to clients."""

import json
import numpy as np
import pandas as pd

ACTIVITY_COLUMNS = ["date", "outlet_id", "product_id", "client_id"]


def encode_activity(sales):
    if sales.empty:
        return {"version": 1, "columns": ACTIVITY_COLUMNS, "rows": []}
    data = sales.copy()
    data["date"] = pd.to_datetime(data.sale_date).dt.to_period("M").astype(str) + "-01"
    data = data[ACTIVITY_COLUMNS].drop_duplicates()
    return {"version": 1, "columns": ACTIVITY_COLUMNS, "rows": json.loads(data.to_json(orient="values"))}


def verified_activity(dataset, sales):
    """Enrich old snapshots only if raw sales reproduce every frozen amount and deal count."""
    required = {"date", "outlet_id", "product_id", "fact", "deals"}
    frozen = pd.DataFrame(dataset["rows"], columns=dataset.get("columns"))
    if not required <= set(frozen):
        raise ValueError("В снимке нет полей для проверки детализации клиентов")
    if frozen.empty and sales.empty:
        return encode_activity(sales)
    if sales.empty:
        if all(pd.to_numeric(frozen[column]).fillna(0).eq(0).all() for column in ["fact", "deals"]):
            return encode_activity(sales)
        raise ValueError("В источнике нет замороженных продаж для проверки")
    data = sales.copy()
    data["date"] = pd.to_datetime(data.sale_date).dt.to_period("M").astype(str) + "-01"
    data["amount"] = pd.to_numeric(data.amount)
    keys = ["date", "outlet_id", "product_id"]
    actual = data.groupby(keys, as_index=False).agg(fact=("amount", "sum"), deals=("amount", "size"))
    joined = frozen[keys + ["fact", "deals"]].merge(
        actual, on=keys, how="outer", validate="one_to_one", suffixes=("_frozen", "_source")
    )
    for column in ["fact", "deals"]:
        old = pd.to_numeric(joined[column + "_frozen"]).fillna(0)
        new = pd.to_numeric(joined[column + "_source"]).fillna(0)
        if not np.allclose(old, new, rtol=1e-10, atol=1e-6):
            raise ValueError(
                "Источник изменился: клиентская детализация не совпала с замороженными продажами"
            )
    return encode_activity(sales)
