"""Native Plotly traces from the same aggregated frame as static Seaborn exports."""

import html
import math
import numpy as np
import pandas as pd
import seaborn as sns
from .registry import Context, COLORS, PALETTE, fmt


def value(v):
    return None if pd.isna(v) or not math.isfinite(float(v)) else float(v)


def scale_label(v, style):
    if style in {"percent", "points"}:
        return fmt(v, style)
    for divisor, suffix in [(1e9, "млрд"), (1e6, "млн"), (1e3, "тыс.")]:
        if abs(v) >= divisor:
            return f"{v / divisor:.1f} {suffix}".replace(".", ",", 1)
    return f"{v:g}".replace(".", ",")


def figure(df, spec, axes):
    ctx = Context(None, df, spec)

    def esc(v):
        return html.escape(str(v), quote=True)

    palette = sns.color_palette(PALETTE).as_hex()

    def color(key, i=0):
        return COLORS.get(key, palette[i % len(palette)])

    labels = [esc(ctx.category(spec.x, v)) for v in df[spec.x]]
    cartesian = spec.type != "donut"
    offset = [0.5, 0.5] if spec.type == "heatmap" else [0, 0]
    layout = {
        "paper_bgcolor": "white",
        "plot_bgcolor": "white",
        "font": {"family": "system-ui, sans-serif", "size": 10, "color": "#394451"},
        "margin": {"l": 65, "r": 18, "t": 8, "b": 42},
        "autosize": True,
        "showlegend": False,
        "dragmode": "zoom",
        "hovermode": "closest",
        "separators": ", ",
        "barmode": "stack" if spec.type == "stacked_bar" else "group",
        "hoverlabel": {"bgcolor": "white", "font": {"size": 12}},
        "xaxis": {"automargin": True, "gridcolor": "#e9ecf1", "zeroline": False, "fixedrange": False},
        "yaxis": {"automargin": True, "gridcolor": "#e9ecf1", "zeroline": False, "fixedrange": False},
    }
    numeric = {"tickformat": ",.0f" if spec.number_format in {"money", "number"} else ".1f"}
    if spec.number_format == "percent":
        numeric["ticksuffix"] = " %"
    if spec.xlabel:
        layout["xaxis"]["title"] = {"text": esc(ctx.label(spec.xlabel))}
    if spec.ylabel:
        layout["yaxis"]["title"] = {"text": esc(ctx.label(spec.ylabel))}
    traces = []
    if spec.type == "heatmap":
        from .statistical import heatmap_frame

        matrix = heatmap_frame(df, spec)
        xs = [esc(ctx.category(spec.x, v)) for v in matrix.columns]
        ys = [
            esc(ctx.category(spec.group, v)) if spec.group else esc(ctx.label(str(v))) for v in matrix.index
        ]
        z = [[value(v) for v in row] for row in matrix.to_numpy()]
        texts = [
            [
                f"{ys[r]} · {xs[c]}<br>{esc(ctx.label(spec.y[0]))}: {esc(fmt(v, spec.number_format))}"
                for c, v in enumerate(row)
            ]
            for r, row in enumerate(z)
        ]
        colors = sns.light_palette("#4675bd", 9).as_hex()
        ticks = np.linspace(min(0, float(matrix.min().min())), float(matrix.max().max()), 3).tolist()
        traces.append(
            {
                "type": "heatmap",
                "x": xs,
                "y": ys,
                "z": z,
                "colorscale": [[i / 8, c] for i, c in enumerate(colors)],
                "zmin": 0 if matrix.min().min() >= 0 else None,
                "hovertext": texts,
                "hovertemplate": "%{hovertext}<extra></extra>",
                "hoverongaps": False,
                "colorbar": {
                    "thickness": 10,
                    "len": 0.9,
                    "tickfont": {"size": 9},
                    "tickvals": ticks,
                    "ticktext": [scale_label(v, spec.number_format) for v in ticks],
                },
                "meta": {"key": spec.y[0]},
                "name": esc(ctx.label(spec.y[0])),
            }
        )
        layout["xaxis"].update(type="category", categoryorder="array", categoryarray=xs)
        layout["yaxis"].update(type="category", categoryorder="array", categoryarray=ys, autorange="reversed")
        layout["yaxis"]["labelalias"] = {s: s[:29] + "…" for s in ys if len(s) > 32}
    elif spec.type == "donut":
        key = spec.y[0]
        traces.append(
            {
                "type": "pie",
                "labels": labels,
                "values": [value(v) for v in df[key]],
                "hole": 0.62,
                "sort": False,
                "direction": "clockwise",
                "textinfo": "none",
                "marker": {
                    "colors": [color(str(v), i) for i, v in enumerate(df[spec.x])],
                    "line": {"color": "white", "width": 2},
                },
                "customdata": [esc(fmt(v, spec.number_format)) for v in df[key]],
                "hovertemplate": "%{label}<br>%{customdata}<br>Доля: %{percent}<extra></extra>",
                "meta": {"key": key},
                "name": esc(ctx.label(key)),
            }
        )
        layout["margin"] = {"l": 8, "r": 8, "t": 8, "b": 8}
        traces[0]["domain"] = {"x": [0, 0.58]}
        layout["showlegend"] = True
        layout["legend"] = {
            "x": 0.62,
            "y": 0.5,
            "yanchor": "middle",
            "font": {"size": 10},
            "itemclick": False,
            "itemdoubleclick": False,
        }
        layout["annotations"] = [
            {
                "text": "100%",
                "showarrow": False,
                "font": {"size": 20},
                "x": 0.29,
                "y": 0.5,
                "xref": "paper",
                "yref": "paper",
                "xanchor": "center",
                "yanchor": "middle",
            }
        ]
    elif spec.type == "histogram":
        key = spec.y[0]
        counts, edges = np.histogram(
            df[key].dropna(), bins=min(20, max(1, int(len(df[key].dropna()) ** 0.5)))
        )
        traces.append(
            {
                "type": "bar",
                "x": ((edges[:-1] + edges[1:]) / 2).tolist(),
                "y": counts.tolist(),
                "width": (np.diff(edges) * 0.9).tolist(),
                "marker": {"color": color(key)},
                "hovertext": [
                    f"{esc(fmt(a))}–{esc(fmt(b))}<br>Количество: {int(n)}"
                    for a, b, n in zip(edges[:-1], edges[1:], counts)
                ],
                "hovertemplate": "%{hovertext}<extra></extra>",
                "meta": {"key": key},
                "name": esc(ctx.label(key)),
            }
        )
        layout["xaxis"].update(numeric)
        layout["yaxis"]["title"] = {"text": "Количество"}
    else:
        horizontal = spec.type == "horizontal_bar"
        for i, key in enumerate(spec.y):
            vals = [value(v) for v in df[key]]
            trace = {
                "name": esc(ctx.label(key)),
                "meta": {"key": key},
                "uid": f"{spec.id}-{key}",
                "visible": "legendonly" if key in spec.hidden else True,
                "customdata": [
                    f"{label}<br>{esc(ctx.label(key))}: {esc(fmt(v, spec.number_format))}"
                    for label, v in zip(labels, df[key])
                ],
                "hovertemplate": "%{customdata}<extra></extra>",
            }
            if spec.type == "scatter":
                trace.update(
                    type="scatter",
                    mode="markers",
                    x=[value(v) for v in df[spec.x]],
                    y=vals,
                    marker={"color": color(key, i), "size": 7},
                )
                layout["yaxis"].update(numeric)
            elif spec.type == "line":
                trace.update(
                    type="scatter",
                    mode="lines+markers",
                    x=labels,
                    y=vals,
                    line={"color": color(key, i), "width": 2},
                    marker={"size": 5},
                )
                layout["xaxis"].update(type="category", categoryorder="array", categoryarray=labels)
                layout["yaxis"].update(numeric)
            else:
                trace.update(
                    type="bar",
                    x=vals if horizontal else labels,
                    y=labels if horizontal else vals,
                    marker={"color": color(key, i)},
                    orientation="h" if horizontal else "v",
                )
                category_axis = layout["yaxis" if horizontal else "xaxis"]
                category_axis.update(type="category", categoryorder="array", categoryarray=labels)
                category_axis["labelalias"] = {s: s[:29] + "…" for s in labels if len(s) > 32}
                if horizontal:
                    category_axis["autorange"] = "reversed"
                layout["xaxis" if horizontal else "yaxis"].update(numeric)
            traces.append(trace)
        if "completion" in spec.y and spec.type == "line":
            layout["shapes"] = [
                {
                    "type": "line",
                    "xref": "paper",
                    "x0": 0,
                    "x1": 1,
                    "y0": 100,
                    "y1": 100,
                    "line": {"color": "#b6bccb", "dash": "dash", "width": 1},
                }
            ]
    full_ranges = [
        [v - offset[0] for v in axes["full_xlim"]],
        [v - offset[1] for v in axes["full_ylim"]],
    ]
    if cartesian:
        layout["xaxis"].update(range=full_ranges[0], autorange=False)
        layout["yaxis"].update(range=full_ranges[1], autorange=False)
    if spec.zoom and cartesian:
        x0, x1, y0, y1 = spec.zoom
        layout["xaxis"].update(range=[x0 - offset[0], x1 - offset[0]], autorange=False)
        yrange = [y0 - offset[1], y1 - offset[1]]
        if spec.type in {"heatmap", "horizontal_bar"}:
            yrange.reverse()
        layout["yaxis"].update(range=yrange, autorange=False)
    return {
        "data": traces,
        "layout": layout,
        "meta": {
            "cartesian": cartesian,
            "offset": offset,
            "kind": spec.type,
            "full_ranges": full_ranges,
            "periods": (sorted(df.period.unique()) if spec.type == "heatmap" else list(df.period.unique()))
            if spec.x == "period"
            and "period" in df
            and spec.type in {"line", "bar", "grouped_bar", "stacked_bar", "heatmap"}
            else [],
        },
    }
