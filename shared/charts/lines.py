import numpy as np
from .registry import register


@register("line", {"x": "category or time", "y": "numeric[]"})
def line(ctx):
    df, spec, ax = ctx.df, ctx.spec, ctx.ax
    x = np.arange(len(df))
    for j, key in enumerate(spec.y):
        ctx.series.append({"id": key, "label": ctx.label(key), "color": ctx.color(key, j)})
        if key in spec.hidden:
            continue
        vals = df[key].to_numpy(dtype=float)
        (curve,) = ax.plot(x, vals, color=ctx.color(key, j), linewidth=2)
        curve.set_gid(f"series_{spec.id}_{key}")
        for i, val in enumerate(vals):
            (artist,) = ax.plot([i], [val], "o", markersize=6, color=ctx.color(key, j))
            ctx.mark(artist, key, ctx.category(spec.x, df.iloc[i][spec.x]), val, i)
    if "completion" in spec.y:
        ax.axhline(100, color="#b6bccb", linestyle="--", linewidth=1)
    tick_count = max(3, min(12, spec.width // 70))
    visible = x[(x >= spec.zoom[0]) & (x <= spec.zoom[1])] if spec.zoom else x
    step = max(1, int(np.ceil(len(visible) / tick_count)))
    ticks = visible[::step]
    ax.set_xticks(ticks, [ctx.category(spec.x, df.iloc[i][spec.x]) for i in ticks], rotation=25, ha="right")
