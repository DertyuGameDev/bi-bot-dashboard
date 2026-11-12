"""Metric units and display modes calculated from grouped, filtered observations."""

import numpy as np
import pandas as pd
from shared.calculations import aggregate

METRIC_LABELS = {
    "amount": "Выручка",
    "discount": "Сумма скидок",
    "deals": "Количество сделок",
    "completion": "Выполнение плана",
    "quantity": "Количество товара",
}
FREQUENCIES = {"month": "M", "quarter": "Q", "year": "Y"}


def metric_format(metric):
    return "percent" if metric == "completion" else "number" if metric in {"quantity", "deals"} else "money"


def selected_series(spec, filters):
    """A global metric/view displays that metric; the stored custom recipe remains intact."""
    if filters.metric != "amount" or filters.value_mode != "absolute":
        spec = spec.model_copy(deep=True)
        spec.y = [column for column in spec.y if column in {"plan", "fact"}] or ["fact"]
        spec.sort = "fact" if spec.sort else ""
    return spec


def view_notice(filters):
    metric, mode = filters["metric"], filters["value_mode"]
    if mode == "percent":
        return (
            "Выполнение плана уже выражено в процентах: сумма факта / сумма плана × 100%."
            if metric == "completion"
            else "Графики — доли от выбранного итога; TOP-N не меняет основание процентов. KPI — итоги в единицах метрики."
        )
    if mode == "dynamics":
        unit = "процентных пунктах" if metric == "completion" else "процентах"
        return f"Графики — изменение к предыдущему календарному периоду в {unit}. В сравнении групп используется последний период. KPI — итоги выбранного диапазона."
    return (
        "Выполнение плана = сумма факта / сумма плана × 100%; цель — 100%." if metric == "completion" else ""
    )


def calendar_changes(table, keys, columns, granularity, points=False, baseline=None):
    """Join the actual preceding calendar bucket, never the preceding available row."""
    previous = (baseline if baseline is not None else table)[keys + columns].copy()
    previous["period"] = (pd.PeriodIndex(previous.period, freq=FREQUENCIES[granularity]) + 1).astype(str)
    previous = previous.rename(columns={column: "_previous_" + column for column in columns})
    result = table.merge(previous, on=keys, how="left", validate="one_to_one")
    for column in columns:
        prior = result.pop("_previous_" + column)
        result[column] = (
            result[column] - prior if points else (result[column] - prior) / prior.replace(0, np.nan) * 100
        )
    return result


def apply_view(out, original, spec, filters, keys, comparison=None):
    spec = spec.model_copy(deep=True)
    metric, mode = filters.metric, filters.value_mode
    if metric in {"discount", "deals"}:
        spec.y = [column for column in spec.y if column != "plan"]
        if not spec.y or spec.y == ["completion"]:
            spec.y = ["fact"]
        spec.sort = "fact" if spec.sort == "completion" else spec.sort
    if metric != "amount" and any(column in {"fact", "plan"} for column in spec.y):
        spec.number_format = metric_format(metric)
    if metric == "completion" and spec.type == "donut":
        spec.type = "horizontal_bar"
    if metric == "completion" and out.fact.isna().any():
        out.attrs["notice"] = "При отсутствующем или нулевом плане выполнение не определено."
    if mode == "absolute":
        return out, spec
    if original.empty:
        spec.number_format = "points" if mode == "dynamics" and metric == "completion" else "percent"
        return out, spec

    # Completion has a native percent unit; it is never divided by a sum of percentages.
    if mode == "percent":
        if metric == "completion":
            spec.number_format = "percent"
        else:
            if spec.y == ["completion"]:
                spec.y = ["fact"]
            for column in spec.y:
                if column in {"fact", "plan", "discount", "quantity", "deals"}:
                    total = out[column].sum(min_count=1)
                    out[column] = out[column] / total * 100 if pd.notna(total) and total else np.nan
            spec.number_format = "percent"
        return out, spec

    # Every panel uses calendar dynamics. Categorical panels compare the final bucket
    # with its predecessor; temporal panels keep each visible bucket in the series.
    if spec.y == ["completion"]:
        spec.y = ["fact"]
    spec.sort = "fact"
    columns = [column for column in spec.y if column in out.columns]
    groups = [key for key in keys if key != "period"]
    temporal = "period" in keys
    source = comparison if comparison is not None else original
    observed = original.attrs.get("observed_end")
    end = min(value for value in [filters.end, observed] if value) if filters.end or observed else None
    last = str(pd.Period(end, freq=FREQUENCIES[filters.granularity])) if end else str(source.period.max())
    source = source.loc[source.period <= last]
    current = original.loc[original.period <= last]
    if end:
        source = source.loc[pd.to_datetime(source.date) <= pd.Timestamp(end)]
        current = current.loc[pd.to_datetime(current.date) <= pd.Timestamp(end)]
    timeline = aggregate(current, [*groups, "period"], metric)
    baseline = aggregate(source, [*groups, "period"], metric)
    # A custom derived series must have an explicit calendar definition of its own.
    missing = set(columns) - set(timeline.columns)
    if missing:
        raise ValueError(
            "Для динамики выберите исходную метрику; вычисляемая серия требует отдельной формулы динамики"
        )
    changed = calendar_changes(
        timeline, [*groups, "period"], columns, filters.granularity, metric == "completion", baseline
    )
    out = changed if temporal else changed.loc[changed.period == last].copy()
    if temporal:
        out = out.loc[out.period.isin(original.period.unique())].copy()
    spec.number_format = "points" if metric == "completion" else "percent"
    if spec.type == "donut":
        spec.type = "horizontal_bar"
    prior = str(pd.Period(last, freq=FREQUENCIES[filters.granularity]) - 1)
    out.attrs["notice"] = f"Сравнение {last} с {prior}." if not temporal else ""
    if out[columns].isna().any().any():
        out.attrs["notice"] += " Без предыдущего периода или при нулевой базе значение не определено."
    return out, spec
