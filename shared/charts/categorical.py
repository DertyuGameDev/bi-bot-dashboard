import numpy as np
import pandas as pd
from .registry import register, fmt


def bars(ctx, horizontal=False, stacked=False):
    ax, df, spec = ctx.ax, ctx.df, ctx.spec
    labels = [ctx.category(spec.x, value) for value in df[spec.x]]
    pos = np.arange(len(df))
    keys = [k for k in spec.y if k not in spec.hidden]
    width = 0.65 if stacked else 0.72 / max(1, len(keys))
    bottom = np.zeros(len(df))
    for j, key in enumerate(spec.y):
        ctx.series.append({"id": key, "label": ctx.label(key), "color": ctx.color(key, j)})
        if key in spec.hidden:
            continue
        vals = df[key].fillna(0).to_numpy()
        offset = pos if stacked else pos + (keys.index(key) - (len(keys) - 1) / 2) * width
        if horizontal:
            artists = ax.barh(offset, vals, height=width, color=ctx.color(key, j), alpha=0.95)
        else:
            artists = ax.bar(
                offset,
                vals,
                width=width,
                bottom=bottom if stacked else None,
                color=ctx.color(key, j),
                alpha=0.95,
            )
        for i, artist in enumerate(artists):
            ctx.mark(artist, key, labels[i], df.iloc[i][key], i)
            if len(df) == 1 and len(keys) <= 2:
                value = df.iloc[i][key]
                if pd.isna(value) or not np.isfinite(value):
                    continue
                if horizontal:
                    ax.annotate(
                        fmt(value, spec.number_format),
                        (value, artist.get_y() + artist.get_height() / 2),
                        xytext=(5, 0),
                        textcoords="offset points",
                        va="center",
                        fontsize=9,
                    )
                else:
                    ax.annotate(
                        fmt(value, spec.number_format),
                        (artist.get_x() + artist.get_width() / 2, value),
                        xytext=(0, 4 if value >= 0 else -4),
                        textcoords="offset points",
                        ha="center",
                        va="bottom" if value >= 0 else "top",
                        fontsize=9,
                    )
        if stacked:
            bottom += vals
    if horizontal:
        ax.set_yticks(pos, [s if len(s) < 30 else s[:27] + "…" for s in labels])
        ax.invert_yaxis()
        ax.figure.subplots_adjust(left=0.34, bottom=0.16)
    else:
        if len(labels) == 1:
            from textwrap import fill

            display = [fill(labels[0].replace("\n", " · "), width=38, max_lines=2, placeholder="…")]
        else:
            display = labels
        tick_count = max(3, min(12, spec.width // 70))
        visible = pos[(pos >= spec.zoom[0]) & (pos <= spec.zoom[1])] if spec.zoom else pos
        step = max(1, int(np.ceil(len(visible) / tick_count)))
        ticks = visible[::step]
        ax.set_xticks(
            ticks,
            [display[i] for i in ticks],
            rotation=25 if len(labels) > 6 else 0,
            ha="right" if len(labels) > 6 else "center",
        )
    if keys:
        ax.margins(x=0.05)
        if len(df) == 1:
            ax.margins(x=0.25 if horizontal else 0.45, y=0.20)


@register("bar", {"x": "category", "y": "numeric[]"})
def vertical(ctx):
    bars(ctx)


@register("grouped_bar", {"x": "category", "y": "numeric[]"})
def grouped(ctx):
    bars(ctx)


@register("stacked_bar", {"x": "category", "y": "numeric[]"})
def stacked(ctx):
    bars(ctx, stacked=True)


@register("horizontal_bar", {"x": "category", "y": "numeric[]"})
def horizontal(ctx):
    bars(ctx, horizontal=True)


@register("donut", {"x": "category", "y": "one nonnegative numeric column"})
def donut(ctx):
    from textwrap import fill

    df, spec = ctx.df, ctx.spec
    key = spec.y[0]
    if (df[key].dropna() < 0).any():
        raise ValueError("Кольцевая диаграмма требует неотрицательных значений")
    if df[key].sum() <= 0 or key in spec.hidden:
        ctx.ax.text(0.5, 0.5, "Нет положительных значений", ha="center", transform=ctx.ax.transAxes)
        return
    values = df[key].fillna(0)
    artists, _ = ctx.ax.pie(
        values,
        startangle=90,
        radius=0.95,
        wedgeprops={"width": 0.26, "edgecolor": "#ffffff", "linewidth": 3},
        colors=[ctx.color(str(x), i) for i, x in enumerate(df[spec.x])],
    )
    for i, artist in enumerate(artists):
        ctx.mark(artist, key, ctx.category(spec.x, df.iloc[i][spec.x]), df.iloc[i][key], i)
    ctx.ax.text(
        0, 0.05, "100%", ha="center", fontsize=min(26, spec.height * 0.13), weight="normal", color="#272b33"
    )
    if spec.height >= 180:
        ctx.ax.text(0, -0.16, "выбранного итога", ha="center", fontsize=9, color="#6b7280")
    ctx.ax.figure.subplots_adjust(left=0.02, right=0.60, bottom=0.05, top=0.96)
    labels = [fill(ctx.category(spec.x, v), width=26) for v in df[spec.x]]
    ctx.ax.legend(artists, labels, loc="center left", bbox_to_anchor=(1.05, 0.5), frameon=False, fontsize=9)
    ctx.series.append({"id": key, "label": ctx.label(key), "color": ctx.color(key)})
