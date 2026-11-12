"""Activity counts and a comparable previous range for the dashboard's KPI cards."""

import pandas as pd
from shared.models import Filters
from shared.calculations import filtered


def active_rows(frame):
    if "deals" in frame:
        mask = frame.deals.fillna(0) > 0
        if "sale_id" in frame and "discount" in frame:
            mask |= frame.discount.fillna(0) != 0
        return frame.loc[mask]
    return frame.loc[frame.fact.notna() & (frame.fact != 0)]


def activity_counts(frame):
    active = active_rows(frame)
    clients = 0 if active.empty else None
    if "client_id" in active:
        clients = int(active.client_id.nunique()) if active.client_id.notna().all() else None
    elif "client" in active:
        labels = active.client.dropna()
        known = labels.loc[
            ~labels.astype(str).str.strip().str.lower().isin(["", "не распределён", "не распределен"])
        ]
        if len(known) == len(active):
            clients = int(known.nunique())
    return {
        "active_clients": clients,
        "active_outlets": int(active["outlet_id" if "outlet_id" in active else "outlet"].nunique()),
        "active_products": int(active["product_id" if "product_id" in active else "product"].nunique()),
    }


def comparison_window(dataset, filters):
    start = pd.Timestamp(filters.start or dataset["query_start"])
    end = pd.Timestamp(filters.end or dataset["query_end"])
    monthly = dataset["kind"] == "sales"
    if monthly:
        start, end = start.to_period("M").start_time, end.to_period("M").end_time.normalize()
    observed = pd.Timestamp(dataset.get("observed_end") or dataset["query_end"])
    effective_end = min(end, observed)
    if start.day == 1 and end.is_month_end:
        length = (end.year - start.year) * 12 + end.month - start.month + 1
        previous_start = start - pd.DateOffset(months=length)
        previous_full_end = start - pd.Timedelta(days=1)
        previous_end = (
            previous_full_end if effective_end == end else effective_end - pd.DateOffset(months=length)
        )
        if monthly and effective_end.is_month_end:
            previous_end = (effective_end.to_period("M") - length).end_time.normalize()
    else:
        length = (end - start).days + 1
        previous_start = start - pd.Timedelta(days=length)
        previous_full_end = start - pd.Timedelta(days=1)
        previous_end = effective_end - pd.Timedelta(days=length)
    return start, end, effective_end, previous_start, previous_end, previous_full_end


def enrich_kpis(state, frame, values, raw, measure):
    dataset, filters = state["dataset"], Filters.model_validate(state["filters"])
    overview_metric = "amount" if filters.metric == "completion" else filters.metric
    overview = measure(frame, filters.model_copy(update={"metric": overview_metric}).model_dump())
    values["totals"] = {key: overview[key] for key in ["plan", "fact", "deviation", "deviation_pct"]}
    values["totals"]["metric"] = overview_metric
    values.update(activity_counts(frame))
    activity = dataset.get("client_activity")
    if activity is not None and dataset["kind"] == "sales":
        selected = active_rows(frame)
        if selected.empty:
            values["active_clients"] = 0
        elif {"date", "outlet_id", "product_id"} <= set(selected):
            members = pd.DataFrame(activity["rows"], columns=activity["columns"])
            cells = selected[["date", "outlet_id", "product_id"]].drop_duplicates()
            matched = members.merge(
                cells, on=["date", "outlet_id", "product_id"], how="inner", validate="many_to_one"
            )
            values["active_clients"] = int(matched.client_id.nunique())
    values["activity_notice"] = (
        "Клиенты учитываются по фактическим продажам, а не по плановым строкам."
        if values["active_clients"] is not None
        else "В этом снимке нет клиентской детализации; условный клиент не считается активным."
    )
    values.update(
        growth=None,
        growth_unit="points" if filters.metric == "completion" else "percent",
        previous_fact=None,
        comparison_period=None,
        growth_notice="",
    )
    start, end, effective, prior_start, prior_end, prior_full_end = comparison_window(dataset, filters)

    def iso(date):
        return date.strftime("%Y-%m-%d")

    values["comparison_period"] = {
        "current_start": iso(start),
        "current_end": iso(effective),
        "start": iso(prior_start),
        "end": iso(prior_end),
        "full_end": iso(prior_full_end),
    }
    if frame.empty or values["fact"] is None or effective < start:
        if effective < start:
            values["comparison_period"] = None
        values["growth_notice"] = "Нет фактических наблюдений в выбранном периоде."
        return values
    if dataset["kind"] == "sales" and effective < end and not effective.is_month_end:
        values["growth_notice"] = "Месяц ещё не завершён; в помесячном снимке нет сопоставимой базы по дням."
        return values
    if iso(prior_start) < dataset["query_start"] or iso(prior_full_end) > dataset["query_end"]:
        values["growth_notice"] = "В загруженном снимке нет полной базы предыдущего периода."
        return values
    previous_filters = filters.model_copy(update={"start": iso(prior_start), "end": iso(prior_end)})
    previous = filtered(raw, previous_filters, monthly=dataset["kind"] == "sales")
    if previous.empty:
        values["growth_notice"] = "Нет наблюдений по этим фильтрам за предыдущий период."
        return values
    previous_fact = measure(previous, previous_filters.model_dump())["fact"]
    if filters.metric == "completion" and effective < end:
        full_filters = previous_filters.model_copy(update={"end": iso(prior_full_end)})
        full = filtered(raw, full_filters, monthly=dataset["kind"] == "sales")
        plan = full.plan.sum(min_count=1)
        fact = previous.fact.sum(min_count=1)
        previous_fact = float(fact / plan * 100) if pd.notna(plan) and plan and pd.notna(fact) else None
    values["previous_fact"] = previous_fact
    if previous_fact is None:
        values["growth_notice"] = "За предыдущий период показатель не определён."
    elif filters.metric == "completion":
        values["growth"] = values["fact"] - previous_fact
    elif previous_fact == 0:
        values["growth_notice"] = "В предыдущем периоде нулевой факт; темп изменения не определён."
    else:
        values["growth"] = (values["fact"] - previous_fact) / previous_fact * 100
    return values
