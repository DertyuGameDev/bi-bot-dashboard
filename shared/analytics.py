from datetime import date
import time
import numpy as np
import pandas as pd
from shared.calculations import filtered, apply_calculations, aggregate
from shared.models import Filters, ChartSpec, DashboardSpec, uid
from shared.source import records
from shared.charts import render
from shared.charts.registry import RENDER_WIDTH
from shared.formatting import markdown_html
from shared.view_modes import apply_view, selected_series, METRIC_LABELS, metric_format, view_notice

COL_LABELS = {
    "date": "Период",
    "region": "Регион",
    "outlet": "Торговая точка",
    "product": "Продукт",
    "category": "Категория",
    "unit": "Ед.",
    "client": "Клиент",
    "fact": "Факт, ₽",
    "plan": "План, ₽",
    "quantity": "Количество",
    "plan_quantity": "План, кол-во",
    "discount": "Скидка, ₽",
    "deals": "Сделки",
    "reason": "Причина скидки",
    "completion": "Выполнение, %",
    "deviation": "Отклонение, ₽",
}


def empty_state():
    return {
        "schema_version": 1,
        "snapshot": None,
        "revision": 0,
        "dataset": None,
        "calculations": [],
        "calculation_history": [],
        "filters": Filters().model_dump(),
        "dashboard": DashboardSpec().model_dump(),
        "messages": [],
        "debug": {"skills": [], "tools": [], "warnings": []},
        "saved_id": None,
        "workspace": None,
    }


def default_charts():
    return [
        ChartSpec(
            id="sales-period", title="План и факт по месяцам", source="period", x="period", y=["plan", "fact"]
        ).model_dump(),
        ChartSpec(
            id="sales-structure",
            type="donut",
            title="Структура продаж",
            source="structure",
            x="category",
            y=["fact"],
        ).model_dump(),
        ChartSpec(
            id="sales-ranking",
            type="horizontal_bar",
            title="Регионы по объёму продаж",
            source="ranking",
            x="region",
            y=["plan", "fact"],
            sort="fact",
        ).model_dump(),
        ChartSpec(
            id="sales-completion",
            type="line",
            title="Динамика выполнения плана",
            source="period",
            x="period",
            y=["completion"],
            number_format="percent",
        ).model_dump(),
    ]


def dataset_frame(state):
    if not state["dataset"]:
        return pd.DataFrame()
    df = pd.DataFrame(state["dataset"]["rows"], columns=state["dataset"].get("columns"))
    for col in ["fact", "plan", "quantity", "plan_quantity", "discount", "deals"]:
        if col not in df:
            df[col] = pd.Series(dtype=float)
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def reconcile_outlets(state):
    """Repair a saved cross-region selection, without broadening valid empty filters."""
    filters = state["filters"]
    if not state.get("dataset") or not (filters["regions"] and filters["outlets"]):
        return
    rows = state["dataset"]["rows"]
    available = {r["outlet"] for r in rows if r.get("region") in filters["regions"]}
    if not available:  # An unknown/empty region is a genuine empty selection.
        return
    compatible = [name for name in filters["outlets"] if name in available]
    removed = len(filters["outlets"]) - len(compatible)
    if not removed:
        return
    filters["outlets"] = compatible
    state["snapshot"] = None
    state["filter_notice"] = f"Убраны точки из других регионов: {removed}. " + (
        f"В выборе осталось точек: {len(compatible)}."
        if compatible
        else "Показаны все точки выбранного региона."
    )
    for chart in state["dashboard"]["charts"]:
        chart["zoom"] = None


def materialize(state):
    reconcile_outlets(state)
    start = time.perf_counter()
    snapshot = state.get("snapshot")
    if (
        snapshot
        and state["dataset"]
        and snapshot["table_id"] == state["dataset"]["id"]
        and snapshot["table_version"] == state["dataset"]["version"]
        and snapshot["filters"] == state["filters"]
        and snapshot["recipes"] == state["calculations"]
    ):
        restored = pd.DataFrame(snapshot["rows"], columns=snapshot.get("columns"))
        for col in snapshot.get("numeric_columns", []):
            restored[col] = pd.to_numeric(restored[col], errors="coerce")
        restored.attrs["observed_end"] = state["dataset"].get("observed_end")
        return restored, snapshot["warnings"], round((time.perf_counter() - start) * 1000, 1)
    df = dataset_frame(state)
    if df.empty:
        if state["dataset"]:
            df, warnings = apply_calculations(df, state["calculations"])
            return df, warnings, 0
        return df, [], 0
    f = Filters.model_validate(state["filters"])
    subset = filtered(df, f, monthly=state["dataset"]["kind"] == "sales")
    subset, warnings = apply_calculations(subset, state["calculations"])
    subset.attrs["observed_end"] = state["dataset"].get("observed_end")
    return subset, warnings, round((time.perf_counter() - start) * 1000, 1)


def comparison_frame(state):
    """Read a preceding bucket from the frozen loaded dataset, without another source request."""
    f = Filters.model_validate(state["filters"])
    if f.value_mode != "dynamics":
        return None
    frequency = {"month": "M", "quarter": "Q", "year": "Y"}[f.granularity]
    start = f.start or state["dataset"]["query_start"]
    prior = (pd.Period(start, freq=frequency) - 1).start_time.strftime("%Y-%m-%d")
    # A partially loaded preceding bucket is not a valid comparison base.
    start = prior if prior >= state["dataset"]["query_start"] else start
    expanded = f.model_copy(update={"start": start})
    base = filtered(dataset_frame(state), expanded, monthly=state["dataset"]["kind"] == "sales")
    return apply_calculations(base, state["calculations"])[0]


def chart_frame(frame, spec, filters, recipes=None, comparison=None):
    f = Filters.model_validate(filters)
    spec = selected_series(spec, f)
    df = frame.copy()
    if "date" not in df:
        df["date"] = pd.Series(dtype=str)
    periods = pd.to_datetime(df.date)
    df["period"] = periods.dt.to_period({"month": "M", "quarter": "Q", "year": "Y"}[f.granularity]).astype(
        str
    )
    if comparison is not None:
        comparison = comparison.copy()
        comparison["period"] = (
            pd.to_datetime(comparison.date)
            .dt.to_period({"month": "M", "quarter": "Q", "year": "Y"}[f.granularity])
            .astype(str)
        )
    if spec.type == "heatmap":
        spec = spec.model_copy(deep=True)
        # Old saved heatmaps used x=outlet without a second axis. Recover the intended time matrix.
        row_key = spec.group or (
            "group" if "group" in df else (f.dimension if spec.x == "period" else spec.x)
        )
        column_key = "period" if row_key == spec.x else spec.x
        if row_key == column_key or row_key not in df or column_key not in df:
            raise ValueError("Тепловой карте нужны два существующих разных разреза: group и x")
        out = aggregate(df, [row_key, column_key], f.metric)
        out = recalculate_grouped(out, df, [row_key, column_key], spec, recipes or [], f)
        out, spec = apply_view(out, df, spec, f, [row_key, column_key], comparison)
        total_groups = out[row_key].nunique()
        if row_key in {"outlet", "region", "product", "client"} and total_groups > f.top_n:
            leaders = (
                out.groupby(row_key)["fact"].sum(min_count=1).sort_values(ascending=False).head(f.top_n).index
            )
            out = out.loc[out[row_key].isin(leaders)].copy()
            subject = {
                "outlet": "торговых точек",
                "region": "регионов",
                "product": "продуктов",
                "client": "клиентов",
            }[row_key]
            out.attrs["notice"] = (
                out.attrs.get("notice", "")
                + " "
                + (
                    f"Показаны {f.top_n} из {total_groups} {subject} с наибольшим объёмом за выбранный период."
                )
            )
        spec.x, spec.group = column_key, row_key
        if f.metric == "quantity" and spec.number_format == "money":
            spec.number_format = "number"
        if all(y in spec.hidden for y in spec.y):
            spec.hidden = []
        if f.metric != "amount" or f.value_mode != "absolute":
            spec.title = live_chart_title(spec, f.model_dump(), recipes or [])
        return label_chart(out.reset_index(drop=True), spec, f, recipes), spec
    key = {"period": "period", "structure": "category", "ranking": f.dimension, "table": spec.x}.get(
        spec.source
    )
    if spec.source == "table" and key == "date":
        key = "period"
    if key is None or key not in df:
        raise ValueError("Неизвестный источник или разрез графика")
    if spec.source == "table":
        raw = (
            f.metric == "amount"
            and f.value_mode == "absolute"
            and key not in {"period", "region", "outlet", "product", "client"}
        )
        out = df.copy() if raw else aggregate(df, key, f.metric)
        if not raw:
            out = recalculate_grouped(out, df, key, spec, recipes or [], f)
    else:
        out = aggregate(df, key, f.metric)
        out = recalculate_grouped(out, df, key, spec, recipes or [], f)
    out, spec = apply_view(out, df, spec, f, [key], comparison)
    if spec.source == "structure" and len(out) > 7:
        out = out.sort_values("fact", ascending=False)
        other = out.iloc[7:].select_dtypes(include="number").sum(min_count=1).to_dict()
        other[key] = "Остальные"
        out = pd.concat([out.head(7), pd.DataFrame([other])], ignore_index=True)
    if spec.source == "ranking":
        sort = spec.sort or "fact"
        if sort not in out:
            raise ValueError(f"Неизвестная сортировка {sort}")
        count = len(out)
        out = out.sort_values(sort, ascending=not spec.descending).head(f.top_n)
        if count > f.top_n:
            out.attrs["notice"] = out.attrs.get("notice", "") + f" Показаны {f.top_n} из {count} групп."
    elif spec.source == "period":
        out = out.sort_values(key)
        # Calendar-aware growth after selected time aggregation; missing periods stay null.
        freq = {"month": "M", "quarter": "Q", "year": "Y"}[f.granularity]
        if len(out) > 1:
            p = pd.PeriodIndex(out[key], freq=freq)
            consecutive = [False] + [(p[i].ordinal - p[i - 1].ordinal) == 1 for i in range(1, len(p))]
            out.loc[~np.array(consecutive), "growth"] = np.nan
    spec = spec.model_copy(deep=True)
    if spec.type != "scatter":
        spec.x = key
    if spec.source == "ranking":
        subject = {
            "region": "Регионы",
            "outlet": "Торговые точки",
            "product": "Продукты",
            "client": "Клиенты",
        }[f.dimension]
        spec.title = subject + (" по выполнению плана" if spec.sort == "completion" else " по объёму продаж")
    if spec.source == "period" and spec.id == "sales-period":
        spec.title = (
            "План и факт по " + {"month": "месяцам", "quarter": "кварталам", "year": "годам"}[f.granularity]
        )
    if f.metric == "quantity" and spec.number_format == "money":
        spec.number_format = "number"
    if spec.source == "ranking" and spec.sort == "completion":
        spec.y = ["completion"]
        spec.number_format = "percent"
    if all(y in spec.hidden for y in spec.y):
        spec.hidden = []
    if f.metric != "amount" or f.value_mode != "absolute":
        spec.title = live_chart_title(spec, f.model_dump(), recipes or [])
    return label_chart(out.reset_index(drop=True), spec, f, recipes), spec


def label_chart(frame, spec, filters, recipes=None):
    label = METRIC_LABELS[filters.metric]
    labels = {
        "fact": "Факт" if filters.metric == "amount" else label,
        "plan": "Цель" if filters.metric == "completion" else "План",
    }
    if filters.value_mode == "percent" and filters.metric != "completion":
        labels.update(fact="Доля: " + label.lower(), plan="Доля плана")
    if filters.value_mode == "dynamics":
        labels.update(fact="Изменение: " + label.lower(), plan="Изменение плана")
    calculated = {recipe["name"]: recipe["label"] for recipe in recipes or []}
    frame.attrs["column_labels"] = {**COL_LABELS, "period": "Период", **calculated}
    frame.attrs["series_labels"] = {**calculated, **labels}
    return frame


def recalculate_grouped(out, original, key, spec, recipes, filters):
    """Never sum row percentages. Re-evaluate requested formulas on grouped inputs."""
    from shared.calculations import formula
    from shared.models import Calculation

    needed = set(spec.y + ([spec.sort] if spec.sort else []))
    for recipe in reversed(recipes):
        if recipe["name"] in needed:
            needed.update(recipe["dependencies"])
    for recipe in recipes:
        r = Calculation.model_validate(recipe)
        if r.name not in needed:
            continue
        if r.kind == "formula":
            out[r.name] = formula(r.expression, out)
        elif r.kind == "share":
            denom = out[r.column].sum(min_count=1)
            out[r.name] = out[r.column] / denom * 100 if denom else np.nan
        elif r.kind in {"mean", "median", "min", "max"}:
            mapping = original.groupby(key)[r.column].agg(r.kind)
            if isinstance(key, list):
                out[r.name] = (
                    out[key]
                    .merge(mapping.rename(r.name).reset_index(), on=key, how="left", validate="many_to_one")[
                        r.name
                    ]
                    .to_numpy()
                )
            else:
                out[r.name] = out[key].map(mapping)
        elif r.kind == "rank":
            out[r.name] = out[r.column].rank(ascending=False, method="dense")
        elif key == "period":
            temp = out.copy()
            freq = {"month": "M", "quarter": "Q", "year": "Y"}[filters.granularity]
            temp["date"] = pd.PeriodIndex(temp[key], freq=freq).to_timestamp().strftime("%Y-%m-%d")
            adjusted = r.model_copy(update={"group_by": []})
            calculated, _ = apply_calculations(temp, [adjusted.model_dump()])
            out[r.name] = calculated[r.name]
        else:
            raise ValueError(
                "Динамику и накопительный итог показывайте в графике по периодам; суммирование процентов по датам недопустимо"
            )
    return out


def kpis(df, filters):
    f = Filters.model_validate(filters)
    if df.empty:
        return {
            "plan": None,
            "fact": None if f.metric == "completion" else 0,
            "completion": None,
            "deviation": None,
            "deviation_pct": None,
            "outlets": 0,
            "products": 0,
            "growth": None,
            "rows": 0,
        }
    # Quantity metrics cannot mix kilograms, litres and pieces.
    if f.metric == "quantity" and df.unit.nunique() > 1:
        raise ValueError("Выберите продукты с одной единицей измерения для сравнения количества")
    actual = f.metric if f.metric in {"quantity", "discount", "deals"} else "fact"
    planned = "plan_quantity" if f.metric == "quantity" else "plan"
    raw_fact = df[actual].sum(min_count=1)
    fact = float(raw_fact) if pd.notna(raw_fact) else None
    plan = (
        float(df[planned].sum(min_count=1))
        if f.metric not in {"discount", "deals"} and df[planned].notna().any()
        else None
    )
    ratio = fact / plan * 100 if fact is not None and plan is not None and plan != 0 else None
    if f.metric == "completion":
        fact, plan = ratio, 100.0 if ratio is not None else None
    return {
        "plan": plan,
        "fact": fact,
        "completion": ratio,
        "deviation": fact - plan if fact is not None and plan is not None else None,
        "deviation_pct": ratio - 100 if ratio is not None else None,
        "outlets": int(df.outlet.nunique()),
        "products": int(df["product"].nunique()),
        "growth": None,
        "rows": len(df),
    }


def dashboard_kpis(state, frame=None, raw=None):
    from shared.kpi_summary import enrich_kpis

    frame = materialize(state)[0] if frame is None else frame
    return enrich_kpis(
        state, frame, kpis(frame, state["filters"]), dataset_frame(state) if raw is None else raw, kpis
    )


def capture_snapshot(state):
    """Freeze the current filtered table, shared by manual and automatic saves."""
    import copy
    from datetime import datetime, timezone

    snapshot = state.get("snapshot")
    if (
        snapshot
        and snapshot["table_id"] == state["dataset"]["id"]
        and snapshot["table_version"] == state["dataset"]["version"]
        and snapshot["filters"] == state["filters"]
        and snapshot["recipes"] == state["calculations"]
    ):
        return

    frame, warnings, _ = materialize(state)
    state["snapshot"] = {
        "schema_version": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "table_id": state["dataset"]["id"],
        "table_version": state["dataset"]["version"],
        "filters": copy.deepcopy(state["filters"]),
        "recipes": copy.deepcopy(state["calculations"]),
        "rows": records(frame),
        "columns": frame.columns.tolist(),
        "numeric_columns": frame.select_dtypes(include="number").columns.tolist(),
        "kpis": dashboard_kpis(state, frame),
        "warnings": warnings,
    }


def live_chart_title(spec, filters, calculations):
    """A live figure cannot retain a region/year baked into the model's original caption."""
    labels = {"fact": "Выручка", "plan": "План", **{c["name"]: c["label"] for c in calculations}}
    labels.update(completion="Выполнение плана", growth="Динамика выручки", discount="Скидки")
    labels["fact"] = METRIC_LABELS[filters["metric"]]
    metric = (
        "План и факт"
        if set(spec.y) == {"plan", "fact"} and filters["metric"] == "amount"
        else ", ".join(labels.get(y, y) for y in spec.y)
    )
    if filters["value_mode"] == "percent" and filters["metric"] != "completion":
        metric = "Доли, % · " + metric
    elif filters["value_mode"] == "dynamics":
        metric = "Динамика · " + METRIC_LABELS[filters["metric"]]
    periods = {"month": "месяцам", "quarter": "кварталам", "year": "годам"}
    groups = {"outlet": "точкам", "region": "регионам", "product": "продуктам", "client": "клиентам"}
    if spec.type == "heatmap":
        axes = {**groups, "period": periods[filters["granularity"]], "category": "категориям"}
        return f"{metric} по {axes.get(spec.group, spec.group)} и {axes.get(spec.x, spec.x)}"
    if spec.source == "period":
        return f"{metric} по {periods[filters['granularity']]}"
    if spec.source == "structure":
        return metric + " по категориям"
    if spec.source == "ranking":
        return f"{metric} по {groups[filters['dimension']]}"
    return f"{metric} · {COL_LABELS.get(spec.x, spec.x)}"


def summary(values, df, filters):
    from shared.charts.registry import fmt

    unit = metric_format(filters["metric"])
    if not len(df):
        return "По выбранным условиям данных нет. Измените период или сбросьте фильтры."
    text = f"Факт — **{fmt(values['fact'], unit)}**"
    if values["completion"] is not None:
        text += f", выполнение плана — **{fmt(values['completion'], 'percent')}**. Отклонение — {fmt(values['deviation'], unit)}"
    text += "."
    region = aggregate(df, "region", filters["metric"]).sort_values("fact", ascending=False)
    if len(region):
        text += f" Лидер по объёму — **{region.iloc[0]['region']}**: {fmt(region.iloc[0]['fact'], unit)}."
    return text


def present(state, include_charts=True, offset=0, limit=60):
    messages = [
        {**m, "id": m.get("id", f"legacy-{i}"), "html": m.get("html", markdown_html(m["content"]))}
        for i, m in enumerate(state["messages"])
    ]
    if not state["dataset"]:
        return {
            "revision": state["revision"],
            "dataset": None,
            "filters": state["filters"],
            "dashboard": state["dashboard"],
            "messages": messages,
            "debug": state["debug"],
            "calculations": [],
            "saved_id": state["saved_id"],
            "workspace": state.get("workspace"),
            "charts": [],
        }
    df, warnings, calc_ms = materialize(state)
    values = dashboard_kpis(state, df)
    charts = []
    if include_charts:
        comparison = comparison_frame(state)
        for conf in state["dashboard"]["charts"]:
            spec = ChartSpec.model_validate(conf)
            if RENDER_WIDTH.get():
                spec = spec.model_copy(update={"width": RENDER_WIDTH.get()})
            data, effective = chart_frame(df, spec, state["filters"], state["calculations"], comparison)
            chart = render(data, effective)
            chart["spec"] = effective.model_dump()
            charts.append(chart)
    source = state["dataset"]
    raw = dataset_frame(state)
    cascade = raw[raw.region.isin(state["filters"]["regions"])] if state["filters"]["regions"] else raw
    options = {
        "regions": sorted(raw.region.dropna().unique().tolist()),
        "outlets": sorted(cascade.outlet.dropna().unique().tolist()),
        "products": sorted(raw["product"].dropna().unique().tolist()),
        "clients": sorted(raw.client.dropna().unique().tolist()),
    }
    labels = {**COL_LABELS, **{x["name"]: x["label"] for x in state["calculations"]}}
    columns = [
        {"name": c, "label": labels.get(c, c), "type": str(df[c].dtype), "nulls": int(df[c].isna().sum())}
        for c in df.columns
        if c not in {"outlet_id", "product_id", "client_id"}
    ]
    if (
        source.get("observed_end")
        and state["filters"].get("end")
        and state["filters"]["end"] > source["observed_end"]
    ):
        warnings.append(
            f"Продажи доступны до {source['observed_end']}; выбранный период содержит дни без наблюдаемых продаж."
        )
    if state["filters"].get("start") and not state["filters"]["start"].endswith("-01"):
        warnings.append("Период округлён до начала месяца для сопоставления с месячным планом.")
    return {
        "revision": state["revision"],
        "dataset": {
            "id": source["id"],
            "version": source["version"],
            "source": source["source"],
            "grain": source["grain"],
            "rows": len(df),
            "loaded_rows": len(raw),
            "columns": columns,
            "preview": records(df.iloc[offset : offset + limit][[c["name"] for c in columns]]),
            "offset": offset,
            "limit": limit,
            "query_start": source["query_start"],
            "query_end": source["query_end"],
            "observed_end": source.get("observed_end"),
            "quality": source.get("quality", {}),
        },
        "filters": state["filters"],
        "filter_notice": state.get("filter_notice", ""),
        "dashboard": state["dashboard"],
        "messages": messages,
        "debug": {
            **state["debug"],
            "queries": source.get("queries", []),
            "warnings": list(
                dict.fromkeys([*source.get("warnings", []), *state["debug"].get("warnings", []), *warnings])
            ),
            "calc_ms": calc_ms,
            "fetch_ms": source.get("fetch_ms", 0),
            "table_id": source["id"],
            "table_version": source["version"],
            "chart_specs": state["dashboard"]["charts"],
        },
        "calculations": state["calculations"],
        "saved_id": state["saved_id"],
        "workspace": state.get("workspace"),
        "kpis": values,
        "summary": summary(values, df, state["filters"]),
        "view_notice": view_notice(state["filters"]),
        "options": options,
        "charts": charts,
    }


def install_dataset(state, result):
    state["dataset"] = {**result, "id": uid(), "version": 1}
    state["snapshot"] = None
    state["calculations"] = []
    state["calculation_history"] = []
    state["saved_id"] = None
    observed = date.fromisoformat(result.get("observed_end", "2025-12-31"))
    # Last completed calendar year relative to observed source data, no fabricated current-year data.
    year = observed.year if observed.month == 12 and observed.day == 31 else observed.year - 1
    default_start = max(f"{year}-01-01", result["query_start"])
    default_end = min(f"{year}-12-31", result["query_end"])
    if default_start > default_end:
        default_start, default_end = result["query_start"], result["query_end"]
    state["filters"] = Filters(start=default_start, end=default_end).model_dump()
    state["dashboard"] = DashboardSpec(
        table_id=state["dataset"]["id"], title=f"Продажи · {year}", table_version=1
    ).model_dump()
