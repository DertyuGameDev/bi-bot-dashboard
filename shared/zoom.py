"""Link temporal X axes by calendar values, preserving each chart's Y scale."""

import numpy as np
import pandas as pd
from shared.models import ChartSpec
from shared.analytics import chart_frame, materialize, comparison_frame
from shared.charts import render

TEMPORAL_TYPES = {"line", "bar", "grouped_bar", "stacked_bar", "heatmap"}


def interpolate(value, xs, ys):
    if len(xs) == 1:
        return float(ys[0] + value - xs[0])
    if value < xs[0]:
        return float(ys[0] + (value - xs[0]) * (ys[1] - ys[0]) / (xs[1] - xs[0]))
    if value > xs[-1]:
        return float(ys[-1] + (value - xs[-1]) * (ys[-1] - ys[-2]) / (xs[-1] - xs[-2]))
    return float(np.interp(value, xs, ys))


def synchronized_updates(state, index, spec):
    if not state["dashboard"].get("sync_zoom"):
        return []
    df, _, _ = materialize(state)
    comparison = comparison_frame(state)
    frequency = {"month": "M", "quarter": "Q", "year": "Y"}[state["filters"]["granularity"]]

    def domain(config):
        frame, effective = chart_frame(df, config, state["filters"], state["calculations"], comparison)
        if effective.x != "period" or effective.type not in TEMPORAL_TYPES or frame.empty:
            return None
        labels = sorted(frame.period.unique()) if effective.type == "heatmap" else list(frame.period.unique())
        # A one-value heatmap is displayed as a single bar, whose centre is 0.
        observed = frame.loc[frame[effective.y].notna().any(axis=1)]
        positions = np.arange(len(labels), dtype=float) + (
            0.5 if effective.type == "heatmap" and len(observed) > 1 else 0
        )
        return frame, effective, positions, pd.PeriodIndex(labels, freq=frequency).asi8

    source = domain(spec)
    if source is None:
        return []
    updates = []
    for target_index, item in enumerate(state["dashboard"]["charts"]):
        if target_index == index:
            continue
        target_spec = ChartSpec.model_validate(item)
        target = domain(target_spec)
        if target is None:
            continue
        if spec.zoom is None:
            changes = {"zoom": None}
        else:
            bounds = [interpolate(value, source[2], source[3]) for value in spec.zoom[:2]]
            xs = [interpolate(value, target[3], target[2]) for value in bounds]
            ys = (
                target_spec.zoom[2:]
                if target_spec.zoom
                else render(target[0], target[1])["metadata"]["axes"]["ylim"]
            )
            changes = {"zoom": [*xs, *sorted(ys)]}
        updates.append((target_index, {**item, **changes}))
    return updates
