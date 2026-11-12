"""Portable, card-scoped downloads. Public snapshots contain no conversation/source rows."""

import html
import io
import json
import re
from pathlib import Path
import pandas as pd
from shared.analytics import materialize, present, chart_frame, comparison_frame
from shared.models import ChartSpec
from shared.charts import render
from shared.charts.registry import fmt


def safe_cell(value):
    return (
        "'" + value
        if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r"))
        else value
    )


def csv_export(state):
    df, _, _ = materialize(state)
    for col in df.select_dtypes(include=["object", "str"]).columns:
        df[col] = df[col].map(safe_cell)
    return "\ufeff" + df.to_csv(index=False)


def xlsx_export(state):
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.styles import Font, PatternFill
    from shared.analytics import COL_LABELS

    frame, _, _ = materialize(state)
    labels = {**COL_LABELS, **{c["name"]: c["label"] for c in state["calculations"]}}
    book = Workbook(write_only=True)
    sheet = book.create_sheet("Данные")
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = (
        f"A1:{__import__('openpyxl').utils.get_column_letter(len(frame.columns))}{len(frame) + 1}"
    )
    header = []
    for name in frame.columns:
        cell = WriteOnlyCell(sheet, value=safe_cell(labels.get(name, name)))
        cell.font = Font(bold=True, color="294665")
        cell.fill = PatternFill("solid", fgColor="EEF3FA")
        header.append(cell)
    sheet.append(header)
    for row in frame.itertuples(index=False, name=None):
        sheet.append([None if pd.isna(v) else safe_cell(v) for v in row])
    recipes = book.create_sheet("Вычисления")
    recipes.append(["Колонка", "Название", "Операция", "Выражение", "База расчёта"])
    for recipe in state["calculations"]:
        recipes.append(
            [safe_cell(str(recipe.get(key, ""))) for key in ["name", "label", "kind", "expression", "base"]]
        )
    fields = book.create_sheet("Параметры")
    fields.append(["Дашборд", safe_cell(state["dashboard"]["title"])])
    fields.append(
        [
            "Экспорт",
            "Значения вычислений рассчитаны по текущим фильтрам; определения сохранены на листе Вычисления.",
        ]
    )
    for key, value in state["filters"].items():
        fields.append([key, safe_cell(json.dumps(value, ensure_ascii=False))])
    fields.append(["Имена колонок", ", ".join(frame.columns)])
    result = io.BytesIO()
    book.save(result)
    return result.getvalue()


def public_data(state):
    view = present(state)
    frame, _, _ = materialize(state)
    comparison = comparison_frame(state)
    charts = []
    for current in view["charts"]:
        spec = ChartSpec.model_validate(current["spec"])
        table, effective = chart_frame(frame, spec, state["filters"], state["calculations"], comparison)
        # Keep all legend series in a standalone file, with the saved visibility applied by its viewer.
        rendered = render(table, effective.model_copy(update={"hidden": []})) if spec.hidden else current
        chart = {key: rendered.get(key) for key in ["id", "svg", "metadata", "notice", "plotly"]}
        chart["spec"] = current["spec"]
        chart["domain"] = list(dict.fromkeys(table[effective.x].astype(str))) if effective.x in table else []
        if spec.type == "heatmap":
            chart["domain"] = sorted(chart["domain"])
        charts.append(chart)
    return {
        "title": state["dashboard"]["title"],
        "filters": view["filters"],
        "kpis": view["kpis"],
        "period": [state["dataset"]["query_start"], state["dataset"]["query_end"]],
        "sync_zoom": state["dashboard"].get("sync_zoom", False),
        "charts": charts,
    }


def html_export(state):
    data = public_data(state)
    root = Path(__file__).resolve().parents[1]
    source = (root / "MY/dashboard.js").read_text()
    helpers = source[source.index("const number =") : source.index("function dashboardSummary(")]
    cards = source[source.index("export function kpiCards(") : source.index("function selectField(")]
    helpers = re.sub(r"\bexport\s+", "", helpers + cards)
    from plotly.offline import get_plotlyjs

    bundle = get_plotlyjs()
    locale = (root / "shared/vendor/plotly-locale-ru.js").read_text()
    widget = re.sub(r"\bexport\s+", "", (root / "shared/chart-widget.js").read_text())
    script = widget + "\n" + (root / "shared/offline-viewer.js").read_text()
    payload = (
        json.dumps(data, ensure_ascii=False, allow_nan=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    title = html.escape(data["title"])
    return f"""<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>{title}</title>
<style> *{{box-sizing:border-box}}body{{font:14px/1.55 system-ui,sans-serif;color:#394451;background:white;margin:0;padding:24px}}.page{{max-width:1440px;margin:auto}}h1{{font-size:24px;margin:0 0 6px}}.scope{{color:#657184;font-size:12px}}main{{display:grid;grid-template-columns:{"minmax(0,1fr)" if state["dashboard"]["layout"] == "one-column" else "repeat(2,minmax(0,1fr))"};gap:20px}}figure{{margin:0;min-width:0}}figcaption{{font-weight:600;margin-bottom:6px}}.plot{{overflow:auto;touch-action:pan-y}}.chart-metric svg{{display:block;width:100%;height:auto}}.chart-plotly{{width:100%;min-width:0}}.chart-tools{{display:flex;align-items:center;flex-wrap:wrap;gap:4px}}.plotly-status{{display:block;font-size:12px;min-height:20px}}.pie-category{{max-width:100%;font:inherit}}svg text{{pointer-events:none;font-family:system-ui!important}}button{{font:inherit;padding:5px 9px;border:1px solid #dce3ec;border-radius:4px;background:white;color:#445d77;margin:3px;cursor:pointer}}button:focus-visible,[tabindex]:focus-visible{{outline:2px solid #6d96ca}}button:disabled{{opacity:.5;cursor:default}}.legend-button[aria-pressed=false]{{text-decoration:line-through;opacity:.5}}.kpi-cards{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin:20px 0}}.kpi-card{{min-width:0}}dt{{font-size:12px;color:#657184}}dd{{margin:0;font-size:22px;font-weight:600}}.kpi-secondary dd{{font-size:15px}}.kpi-card small{{font-size:11px}}.kpi-positive{{color:#16804e}}.kpi-negative{{color:#b53e3e}}.tools{{display:flex;align-items:center;flex-wrap:wrap}}output{{display:block;min-height:24px;font-size:12px}}table{{border-collapse:collapse;font-size:12px}}td,th{{padding:5px 8px;border-bottom:1px solid #eee;text-align:left}}.values{{overflow:auto;max-height:220px}}#summary{{margin-top:20px;padding-top:12px;border-top:1px solid #e5eaf0}}#summary p{{margin:4px 0}}details{{font-size:12px}}@media(max-width:700px){{body{{padding:12px}}main{{grid-template-columns:1fr}}.kpi-cards{{grid-template-columns:repeat(2,minmax(0,1fr))}}button{{min-width:44px;min-height:44px}}}}@media(prefers-reduced-motion:reduce){{*{{scroll-behavior:auto!important}}}}</style>
<div class="page"><h1>{title}</h1><p class="scope" id="scope"></p><div id="kpis"></div><p class="scope">Снимок дашборда. Фильтры зафиксированы. Наведение — точные значения. Колесо и выделение — масштаб, «Перемещение» — сдвиг области, 0 — сброс. В кольцевой диаграмме можно выделить сектор.</p><label><input id="sync" type="checkbox"> Связать масштаб по времени</label><main></main><section id="summary" aria-label="Краткий итог"><h2>Краткий итог</h2></section></div><script id="snapshot" type="application/json">{payload}</script><script>{bundle}</script><script>{locale}</script><script>{helpers}\n{script}</script></html>"""


def png_export(state):
    from PIL import Image, ImageDraw, ImageFont
    from matplotlib import font_manager
    from textwrap import wrap

    view = present(state)
    frame, _, _ = materialize(state)
    comparison = comparison_frame(state)
    path = font_manager.findfont("DejaVu Sans")

    def font(size):
        return ImageFont.truetype(path, size)

    width, gap, pad = 2000, 36, 50
    columns = 1 if len(view["charts"]) == 1 or state["dashboard"]["layout"] == "one-column" else 2
    chart_width = (width - pad * 2 - gap * (columns - 1)) // columns
    charts = []
    for item in view["charts"]:
        spec = ChartSpec.model_validate(item["spec"]).model_copy(update={"width": 800, "height": 360})
        table, effective = chart_frame(frame, spec, state["filters"], state["calculations"], comparison)
        rendered = render(table, effective, "png")
        if rendered.get("png"):
            chart = Image.open(io.BytesIO(rendered["png"])).convert("RGB")
            chart = chart.resize(
                (chart_width, round(chart.height * chart_width / chart.width)), Image.Resampling.LANCZOS
            )
        else:
            chart = Image.new("RGB", (chart_width, 250), "white")
            ImageDraw.Draw(chart).text(
                (20, 70), rendered.get("notice", "Нет данных"), fill="#657184", font=font(23)
            )
        charts.append((item["spec"]["title"], chart))
    rows = [charts[i : i + columns] for i in range(0, len(charts), columns)]
    title_heights = [max(len(wrap(title, 55)) for title, _ in row) * 30 + 16 for row in rows]
    row_heights = [max(c.height for _, c in row) + height + 30 for row, height in zip(rows, title_heights)]
    title_lines = wrap(state["dashboard"]["title"], 90)
    f = state["filters"]
    scope = (
        f"{f['start'] or state['dataset']['query_start']} — {f['end'] or state['dataset']['query_end']}"
        + (" · " + ", ".join(f["regions"]) if f["regions"] else " · Все регионы")
    )
    scope_lines = wrap(scope, 135)
    extra = max(0, len(title_lines) - 1) * 42 + max(0, len(scope_lines) - 1) * 30
    image = Image.new("RGB", (width, 400 + extra + sum(row_heights) + pad), "white")
    draw = ImageDraw.Draw(image)
    draw.multiline_text((pad, 28), "\n".join(title_lines), font=font(32), fill="#29364a", spacing=10)
    draw.multiline_text(
        (pad, 85 + max(0, len(title_lines) - 1) * 42),
        "\n".join(scope_lines),
        font=font(21),
        fill="#657184",
        spacing=8,
    )
    k = view["kpis"]
    totals = k.get("totals", k)
    metric = totals.get("metric", f["metric"])
    style = "money" if metric in {"amount", "discount"} else "percent" if metric == "completion" else "number"
    entries = [
        ("План", fmt(totals["plan"], style)),
        ("Факт", fmt(totals["fact"], style)),
        ("Выполнение", fmt(k["completion"], "percent")),
        ("Отклонение", fmt(totals["deviation"], style)),
        ("Активные клиенты", fmt(k.get("active_clients")).split(",")[0]),
        ("Активные точки", fmt(k.get("active_outlets")).split(",")[0]),
        ("Активные продукты", fmt(k.get("active_products")).split(",")[0]),
        ("Динамика", fmt(k.get("growth"), "points" if k.get("growth_unit") == "points" else "percent")),
    ]
    for i, (label, value) in enumerate(entries):
        x, y = pad + (i % 4) * ((width - 2 * pad) // 4), 146 + extra + (i // 4) * 95
        draw.text((x, y), label, font=font(19), fill="#657184")
        draw.text((x, y + 30), value, font=font(25), fill="#294665")
    y = 370 + extra
    for row, row_height, title_height in zip(rows, row_heights, title_heights):
        for column, (title, chart) in enumerate(row):
            x = pad + column * (chart_width + gap)
            draw.multiline_text((x, y), "\n".join(wrap(title, 55)), font=font(22), fill="#394451", spacing=6)
            image.paste(chart, (x, y + title_height))
        y += row_height
    result = io.BytesIO()
    image.save(result, format="PNG")
    return result.getvalue()
