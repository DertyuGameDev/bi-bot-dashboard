import io
import re
from contextvars import ContextVar
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
import pandas as pd
import seaborn as sns
from shared.models import ChartSpec

ET.register_namespace("", "http://www.w3.org/2000/svg")
ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
RENDER_WIDTH = ContextVar("render_width", default=None)
RENDER_DASHBOARD = ContextVar("render_dashboard", default=None)
RENDER_VERSION = 5
REGISTRY = {}
LOCK = threading.RLock()
COLORS = {
    "plan": "#b7c5e2",
    "fact": "#4675bd",
    "completion": "#348971",
    "deviation": "#d66b58",
    "discount": "#d99c42",
    "quantity": "#21836b",
}
LABELS = {
    "plan": "План",
    "fact": "Факт",
    "completion": "Выполнение",
    "deviation": "Отклонение",
    "growth": "Динамика",
    "discount": "Скидки",
    "quantity": "Количество",
    "deals": "Количество сделок",
    "plan_quantity": "План, количество",
    "period": "Период",
    "date": "Дата",
    "region": "Регион",
    "outlet": "Торговая точка",
    "product": "Продукт",
    "category": "Категория",
    "client": "Клиент",
}
PALETTE = ["#4675bd", "#b7c5e2", "#348971", "#b476a5", "#ca9b55", "#7b98c9", "#9c88b7", "#7ca894"]


def register(name, parameters):
    def decorator(fn):
        REGISTRY[name] = {"render": fn, "parameters": parameters}
        return fn

    return decorator


def fmt(value, style="number"):
    if pd.isna(value):
        return "нет данных"
    text = f"{float(value):,.2f}".replace(",", " ").replace(".", ",")
    return text + (
        " ₽" if style == "money" else " %" if style == "percent" else " п.п." if style == "points" else ""
    )


@dataclass
class Context:
    ax: object
    df: pd.DataFrame
    spec: ChartSpec
    tips: dict = field(default_factory=dict)
    series: list = field(default_factory=list)

    def label(self, key):
        columns = self.df.attrs.get("column_labels", {})
        return self.df.attrs.get("series_labels", {}).get(key, columns.get(key, LABELS.get(key, key)))

    def category(self, key, value):
        text = str(value)
        quarter = re.fullmatch(r"(\d{4})Q([1-4])", text) if key in {"date", "period"} else None
        if quarter:
            return f"{['I', 'II', 'III', 'IV'][int(quarter[2]) - 1]} кв. {quarter[1]}"
        return text

    def mark(self, artist, key, label, value, index, style=None):
        gid = f"mark_{self.spec.id}_{key}_{index}"
        artist.set_gid(gid)
        self.tips[gid] = {
            "series": key,
            "label": str(label),
            "value": None if pd.isna(value) else float(value),
            "text": f"{label} · {self.label(key)}: {fmt(value, style or self.spec.number_format)}",
        }

    def color(self, key, index=0):
        return COLORS.get(key, PALETTE[index % len(PALETTE)])


def render(df: pd.DataFrame, spec: ChartSpec, output="svg"):
    if output not in {"svg", "png"}:
        raise ValueError("Неизвестный формат графика")
    start = time.perf_counter()
    if spec.type not in REGISTRY:
        raise ValueError(f"Неизвестный тип графика: {spec.type}")
    missing = set([spec.x, *spec.y]) - set(df.columns)
    if missing:
        raise ValueError(f"Нет колонок: {', '.join(sorted(missing))}")
    if all(y in spec.hidden for y in spec.y):
        spec = spec.model_copy(update={"hidden": []})
    mark_limit = 1000 if spec.type == "scatter" else 500
    if spec.type not in {"histogram", "heatmap"} and len(df) > mark_limit:
        raise ValueError(
            f"Для {spec.type} слишком много категорий/точек ({len(df)}). Сгруппируйте данные или сузьте фильтр; строки не усечены."
        )
    for col in spec.y:
        if not pd.api.types.is_numeric_dtype(df[col]):
            raise ValueError(f"Метрика {col} должна быть числовой")
    # Empty data has no marks. A single measured value is still a valid column.
    usable = df.loc[df[spec.y].notna().any(axis=1) & df[spec.x].notna()]
    points = len(usable) if spec.type == "histogram" else usable[spec.x].nunique()
    if spec.type in {"scatter", "heatmap"}:
        group = spec.group or ("group" if "group" in usable else "")
        keys = list(dict.fromkeys([spec.x, *spec.y, *([group] if group else [])]))
        points = len(usable[keys].drop_duplicates())
    if spec.type == "donut":
        if not usable.empty and not (usable[spec.y[0]] > 0).any():
            spec = spec.model_copy(update={"type": "bar"})
        elif points > 1:
            points = usable.loc[usable[spec.y[0]] > 0, spec.x].nunique()
    if points == 0:
        return {
            "id": spec.id,
            "status": "empty",
            "svg": "",
            "png": b"" if output == "png" else None,
            "values": [],
            "label": "",
            "notice": df.attrs.get("notice", "Нет данных для выбранных условий."),
            "metadata": {"tips": {}, "series": [], "axes": {}, "width": spec.width, "height": spec.height},
            "diagnostics": {"renderer": "empty", "rows": len(df), "marks": 0, "render_ms": 0},
        }
    if points == 1 and spec.type in {"line", "heatmap", "donut"}:
        if spec.type == "heatmap" and spec.group:
            df = df.copy()
            df["_single_label"] = df[spec.x].astype(str) + "\n" + df[spec.group].astype(str)
            spec = spec.model_copy(update={"x": "_single_label"})
        spec = spec.model_copy(update={"type": "bar", "xlabel": "", "ylabel": ""})
    if spec.type == "horizontal_bar":
        spec = spec.model_copy(update={"height": max(spec.height, min(1600, len(df) * 18 + 36))})
    min_width = None
    if spec.type == "heatmap":
        from .statistical import heatmap_layout

        width, height = heatmap_layout(df, spec)
        spec = spec.model_copy(update={"width": width, "height": height})
        min_width = width
    with (
        LOCK,
        plt.rc_context(
            {
                "font.family": "DejaVu Sans",
                "font.size": 10,
                "svg.fonttype": "none",
                "axes.spines.top": False,
                "axes.spines.right": False,
                "axes.spines.left": False,
                "axes.spines.bottom": False,
                "axes.labelcolor": "#6b7280",
                "xtick.color": "#6b7280",
                "ytick.color": "#6b7280",
                "text.color": "#272b33",
                "axes.titleweight": "normal",
            }
        ),
    ):
        sns.set_palette(PALETTE)
        fig, ax = plt.subplots(figsize=(spec.width / 72, spec.height / 72), dpi=72)
        fig.patch.set_alpha(0)
        ax.set_facecolor("#ffffff")
        fig.subplots_adjust(left=0.13, bottom=0.23, right=0.97, top=0.93)
        if spec.height < 180 and spec.type not in {"donut", "heatmap", "horizontal_bar"}:
            # Rotated month labels need a fixed text margin even in a short dashboard row.
            fig.subplots_adjust(bottom=min(0.48, 40 / spec.height))
        ctx = Context(ax, df, spec)
        zoom_artists = set()
        full_xlim = full_ylim = None
        if df.empty:
            ax.text(
                0.5,
                0.5,
                "Нет данных за выбранный период",
                ha="center",
                va="center",
                transform=ax.transAxes,
                color="#747984",
            )
            ax.set_axis_off()
        else:
            REGISTRY[spec.type]["render"](ctx)
            # Include painted heatmap cells, annotations and reference lines in the
            # immediate wheel preview, as well as the transparent tooltip targets.
            for i, artist in enumerate([*ax.lines, *ax.patches, *ax.collections, *ax.texts]):
                if not artist.get_gid():
                    artist.set_gid(f"zoom_{spec.id}_{i}")
                zoom_artists.add(artist.get_gid())
                if artist in ax.texts:
                    artist.set_clip_on(True)
            compact = spec.height < 180
            if spec.xlabel:
                fig.subplots_adjust(bottom=max(fig.subplotpars.bottom, min(0.58, 56 / spec.height)))
            ax.set_xlabel(ctx.label(spec.xlabel), fontsize=8 if compact else 10)
            ylabel = ctx.label(spec.ylabel)
            if compact:
                from textwrap import fill

                ylabel = fill(ylabel, width=12, break_long_words=False)
            ax.set_ylabel(ylabel, fontsize=8 if compact else 10)
            if spec.type not in {"donut", "heatmap"}:
                ax.grid(axis="x" if spec.type == "horizontal_bar" else "y", color="#e9ecf1", linewidth=0.7)
                ax.set_axisbelow(True)

                def tick(v, _):
                    suffix = "млн" if abs(v) >= 1e6 else "тыс." if abs(v) >= 1000 else ""
                    div = 1e6 if abs(v) >= 1e6 else 1000 if abs(v) >= 1000 else 1
                    return f"{v / div:g} {suffix}".strip().replace(".", ",")

                (ax.xaxis if spec.type == "horizontal_bar" else ax.yaxis).set_major_formatter(
                    FuncFormatter(tick)
                )
            full_xlim, full_ylim = list(ax.get_xlim()), list(ax.get_ylim())
            if spec.zoom:
                x0, x1, y0, y1 = spec.zoom
                if x0 >= x1 or y0 >= y1:
                    raise ValueError("Некорректная область масштаба")
                inverted_y = ax.yaxis_inverted()
                ax.set_xlim(x0, x1)
                ax.set_ylim(y1, y0) if inverted_y else ax.set_ylim(y0, y1)
        if output == "png" and spec.type != "donut":
            from matplotlib.patches import Patch

            visible = [series for series in ctx.series if series["id"] not in spec.hidden]
            if visible:
                ax.legend(
                    handles=[Patch(color=v["color"], label=v["label"]) for v in visible],
                    loc="lower center",
                    bbox_to_anchor=(0.5, 1.01),
                    ncol=min(4, len(visible)),
                    frameon=False,
                    fontsize=9,
                )
                fig.subplots_adjust(top=0.87)
        fig.canvas.draw()
        bounds = ax.get_window_extent()
        axes = {
            "left": bounds.x0,
            "top": spec.height - bounds.y1,
            "width": bounds.width,
            "height": bounds.height,
            "xlim": list(ax.get_xlim()),
            "ylim": list(ax.get_ylim()),
            "full_xlim": full_xlim or list(ax.get_xlim()),
            "full_ylim": full_ylim or list(ax.get_ylim()),
        }
        if output == "png":
            out = io.BytesIO()
            fig.savefig(
                out,
                format="png",
                dpi=150,
                facecolor="#ffffff",
                metadata={"Software": "Puzzle RPA / Matplotlib + Seaborn"},
            )
        else:
            out = io.StringIO()
            fig.savefig(out, format="svg", transparent=True, metadata={"Date": None})
        plt.close(fig)
    if output == "png":
        return {
            "id": spec.id,
            "png": out.getvalue(),
            "metadata": {
                "axes": axes,
                "tips": ctx.tips,
                "series": ctx.series,
                "width": spec.width,
                "height": spec.height,
            },
            "diagnostics": {
                "renderer": "Matplotlib + Seaborn",
                "rows": len(df),
                "marks": len(ctx.tips),
                "render_ms": round((time.perf_counter() - start) * 1000, 1),
            },
        }
    root = ET.fromstring(out.getvalue())
    root.set("role", "img")
    root.set("aria-label", spec.title)
    for elem in root.iter():
        gid = elem.get("id")
        if gid in zoom_artists:
            elem.set("data-zoom-layer", "true")
        if gid in ctx.tips:
            elem.set("data-series", ctx.tips[gid]["series"])
            elem.set("data-mark", gid)
            elem.set("pointer-events", "all")
            elem.set("tabindex", "0")
            elem.set("aria-label", ctx.tips[gid]["text"])
            title = ET.SubElement(elem, "{http://www.w3.org/2000/svg}title")
            title.text = ctx.tips[gid]["text"]
        elif gid and gid.startswith(f"series_{spec.id}_"):
            elem.set("data-series", gid[len(f"series_{spec.id}_") :])
    from .interactive import figure

    return {
        "id": spec.id,
        "status": "ready",
        "plotly": figure(df, spec, axes),
        "notice": df.attrs.get("notice", ""),
        "svg": ET.tostring(root, encoding="unicode"),
        "metadata": {
            "min_width": min_width,
            "axes": axes,
            "tips": ctx.tips,
            "series": ctx.series,
            "width": spec.width,
            "height": spec.height,
        },
        "diagnostics": {
            "renderer": "Matplotlib + Seaborn",
            "rows": len(df),
            "marks": len(ctx.tips),
            "render_ms": round((time.perf_counter() - start) * 1000, 1),
        },
    }
