import pandas as pd
import seaborn as sns
from .registry import register, fmt


@register("scatter", {"x": "numeric", "y": "numeric[]"})
def scatter(ctx):
    df, spec, ax = ctx.df, ctx.spec, ctx.ax
    if not pd.api.types.is_numeric_dtype(df[spec.x]):
        raise ValueError("Ось X диаграммы рассеяния должна быть числовой")
    for j, key in enumerate(spec.y):
        ctx.series.append({"id": key, "label": ctx.label(key), "color": ctx.color(key, j)})
        if key in spec.hidden:
            continue
        for i, (_, row) in enumerate(df.iterrows()):
            art = ax.scatter(row[spec.x], row[key], s=30, color=ctx.color(key, j), alpha=0.8)
            ctx.mark(art, key, f"{ctx.label(spec.x)}: {fmt(row[spec.x])}", row[key], i)


@register("histogram", {"x": "numeric", "y": "one numeric column"})
def histogram(ctx):
    key = ctx.spec.y[0]
    ctx.series.append({"id": key, "label": ctx.label(key), "color": ctx.color(key)})
    if key in ctx.spec.hidden:
        return
    values = ctx.df[key].dropna()
    counts, bins, artists = ctx.ax.hist(
        values, bins=min(20, max(1, int(len(values) ** 0.5))), color=ctx.color(key), rwidth=0.9
    )
    for i, art in enumerate(artists):
        ctx.mark(art, key, f"{fmt(bins[i])}–{fmt(bins[i + 1])}", counts[i], i, "number")


def heatmap_frame(df, spec):
    key = spec.y[0]
    group = spec.group or ("group" if "group" in df else "")
    if group:
        return df.pivot_table(
            index=group, columns=spec.x, values=key, aggfunc=lambda values: values.sum(min_count=1), sort=True
        )
    if df[spec.x].duplicated().any():
        raise ValueError("Для повторяющихся категорий тепловой карты задайте второй разрез group")
    return df.set_index(spec.x)[[key]].T


def heatmap_layout(df, spec):
    from .registry import RENDER_DASHBOARD

    frame = heatmap_frame(df, spec)
    if frame.size > 2500:
        raise ValueError(
            "Тепловая карта содержит более 2500 ячеек. Сузьте диапазон или группировку; данные не усечены."
        )
    compact = bool(RENDER_DASHBOARD.get())
    width = min(
        1200, max(spec.width, (180 + 36 * len(frame.columns)) if compact else (220 + 48 * len(frame.columns)))
    )
    height = min(800, max(spec.height, (70 + 18 * len(frame)) if compact else (110 + 26 * len(frame))))
    return width, height


@register("heatmap", {"x": "columns", "group": "rows", "y": "one numeric column"})
def heatmap(ctx):
    from matplotlib.colors import LinearSegmentedColormap
    from matplotlib.ticker import FuncFormatter
    from matplotlib.patches import Rectangle
    import pandas as pd

    key = ctx.spec.y[0]
    frame = heatmap_frame(ctx.df, ctx.spec)
    if frame.empty:
        return
    # A blue lower bound keeps low/constant positive values distinguishable from white missing cells.
    cmap = LinearSegmentedColormap.from_list("my_heatmap", ["#cbdff6", "#6d9fd2", "#174f97"])

    def short(value):
        if pd.isna(value):
            return ""
        value = float(value)
        if abs(value) >= 1e6:
            return f"{value / 1e6:.1f}м".replace(".", ",")
        if abs(value) >= 1000:
            return f"{value / 1000:.0f}к"
        return f"{value:.0f}"

    annotations = frame.map(short).to_numpy() if frame.size <= 500 else False
    artist = sns.heatmap(
        frame,
        ax=ctx.ax,
        cmap=cmap,
        cbar=True,
        cbar_kws={"pad": 0.025, "fraction": 0.03},
        annot=annotations,
        fmt="",
        annot_kws={"fontsize": 8},
        linewidths=0.5,
        linecolor="#ffffff",
        vmin=0 if frame.min().min() >= 0 else None,
    )
    artist.collections[0].colorbar.ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: short(value)))
    rows = [ctx.category(ctx.spec.group, v) if ctx.spec.group else ctx.label(str(v)) for v in frame.index]
    columns = [ctx.category(ctx.spec.x, v) for v in frame.columns]
    ctx.ax.set_yticklabels([v if len(v) <= 32 else v[:29] + "…" for v in rows], rotation=0, fontsize=9)
    ctx.ax.set_xticklabels(columns, rotation=40, ha="right", fontsize=9)
    ctx.ax.set_xlabel(ctx.spec.xlabel or "Месяц")
    ctx.ax.set_ylabel(ctx.spec.ylabel or "")
    ctx.ax.figure.subplots_adjust(left=0.30, right=0.94, bottom=0.20, top=0.96)
    for r in range(len(frame)):
        for c in range(len(frame.columns)):
            value = frame.iloc[r, c]
            if pd.isna(value):
                continue
            # Transparent artist contains no paint; it cannot cover the visible heatmap.
            art = Rectangle((c, r), 1, 1, facecolor="none", edgecolor="none", zorder=10)
            ctx.ax.add_patch(art)
            ctx.mark(art, key, f"{rows[r]} · {columns[c]}", value, r * len(frame.columns) + c)
    ctx.series.append({"id": key, "label": ctx.label(key), "color": "#174f97"})
