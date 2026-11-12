import xml.etree.ElementTree as ET
import pandas as pd
import pytest
import re
from shared.charts import render
from shared.models import ChartSpec


def test_horizontal_plan_and_fact_bars_do_not_cover_each_other():
    result = render(
        pd.DataFrame({"region": ["A"], "plan": [100.0], "fact": [80.0]}),
        ChartSpec(type="horizontal_bar", x="region", y=["plan", "fact"]),
    )
    root = ET.fromstring(result["svg"])
    ranges = []
    for group in root.findall(".//{http://www.w3.org/2000/svg}g[@data-mark]"):
        path = group.find("{http://www.w3.org/2000/svg}path")
        coords = [float(value) for value in re.findall(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", path.get("d"))]
        ranges.append((min(coords[1::2]), max(coords[1::2])))
    assert len(ranges) == 2
    assert ranges[0][1] <= ranges[1][0] or ranges[1][1] <= ranges[0][0]


@pytest.mark.parametrize(
    "kind",
    [
        "line",
        "bar",
        "grouped_bar",
        "stacked_bar",
        "horizontal_bar",
        "scatter",
        "histogram",
        "heatmap",
        "donut",
    ],
)
def test_renderer_returns_svg_precise_targets_and_axes(kind):
    df = pd.DataFrame({"x": [1.0, 2.0, 3.0], "fact": [15.0, 20.0, 25.0], "plan": [20.0, 22.0, 23.0]})
    spec = ChartSpec(id="test", type=kind, x="x", y=["fact"])
    result = render(df, spec)
    root = ET.fromstring(result["svg"])
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    assert result["metadata"]["tips"]
    assert result["metadata"]["axes"]["width"] > 0
    assert result["diagnostics"]["renderer"] == "Matplotlib + Seaborn"
    assert "data-mark=" in result["svg"]


def test_hidden_series_and_zoom_recalculate_metadata():
    df = pd.DataFrame({"period": ["A", "B"], "fact": [10.0, 20.0], "plan": [15.0, 25.0]})
    result = render(df, ChartSpec(id="test", hidden=["plan"], zoom=[-0.3, 0.8, 5.0, 22.0]))
    assert all(v["series"] == "fact" for v in result["metadata"]["tips"].values())
    assert result["metadata"]["axes"]["xlim"] == [-0.3, 0.8]
    assert result["metadata"]["axes"]["ylim"] == [5.0, 22.0]


def test_builtin_axes_and_scatter_tooltips_use_russian_labels_and_decimal_comma():
    result = render(
        pd.DataFrame({"quantity": [0.1, 0.2], "deals": [0.1, 0.2]}),
        ChartSpec(
            type="scatter",
            x="quantity",
            y=["deals"],
            xlabel="quantity",
            ylabel="deals",
            number_format="number",
        ),
    )
    texts = [e.text for e in ET.fromstring(result["svg"]).iter() if e.tag.endswith("}text")]
    assert "Количество" in texts and "Количество сделок" in texts
    assert any(text.startswith("0,1") for text in texts if text)
    assert result["metadata"]["series"][0]["label"] == "Количество сделок"
    tip = next(iter(result["metadata"]["tips"].values()))
    assert tip["text"] == "Количество: 0,10 · Количество сделок: 0,10"


@pytest.mark.parametrize("kind", ["line", "bar", "heatmap"])
def test_quarter_labels_are_russian_without_changing_period_keys(kind):
    frame = pd.DataFrame({"period": ["2025Q1", "2025Q2"], "fact": [10.0, 20.0]})
    result = render(frame, ChartSpec(type=kind, y=["fact"]))
    texts = [e.text for e in ET.fromstring(result["svg"]).iter() if e.tag.endswith("}text")]
    assert "I кв. 2025" in texts and "II кв. 2025" in texts
    assert "fact" not in texts
    assert any("I кв. 2025" in tip["text"] for tip in result["metadata"]["tips"].values())
    assert frame.period.tolist() == ["2025Q1", "2025Q2"]


def test_empty_chart_and_bad_spec():
    result = render(
        pd.DataFrame({"period": pd.Series(dtype=str), "fact": pd.Series(dtype=float)}), ChartSpec(y=["fact"])
    )
    assert result["status"] == "empty" and result["svg"] == ""
    with pytest.raises(ValueError):
        render(pd.DataFrame({"x": [1], "fact": [2.0]}), ChartSpec(x="missing"))


def test_scatter_limit_is_explicit_not_silent_truncation():
    df = pd.DataFrame({"x": range(1001), "fact": range(1001)})
    with pytest.raises(ValueError, match="не усечены"):
        render(df, ChartSpec(type="scatter", x="x", y=["fact"]))


@pytest.mark.parametrize(
    "kind",
    [
        "line",
        "bar",
        "grouped_bar",
        "stacked_bar",
        "horizontal_bar",
        "scatter",
        "histogram",
        "heatmap",
        "donut",
    ],
)
def test_all_registered_charts_support_native_png_reply(kind):
    df = pd.DataFrame({"x": [1.0, 2.0, 3.0], "fact": [15.0, 20.0, 25.0], "plan": [20.0, 22.0, 23.0]})
    result = render(df, ChartSpec(type=kind, x="x", y=["fact"]), output="png")
    assert result["png"].startswith(b"\x89PNG\r\n\x1a\n")
    assert result["metadata"]["tips"]
    assert result["diagnostics"]["renderer"] == "Matplotlib + Seaborn"


def test_horizontal_chart_zoom_preserves_ranking_axis_direction():
    frame = pd.DataFrame({"region": ["A", "B"], "fact": [100.0, 50.0]})
    spec = ChartSpec(type="horizontal_bar", x="region", y=["fact"], zoom=[10.0, 80.0, -0.5, 1.5])
    result = render(frame, spec, output="png")
    assert result["metadata"]["axes"]["ylim"] == [1.5, -0.5]


@pytest.mark.parametrize(
    "kind",
    [
        "line",
        "bar",
        "grouped_bar",
        "stacked_bar",
        "horizontal_bar",
        "scatter",
        "histogram",
        "heatmap",
        "donut",
    ],
)
def test_one_observation_is_a_real_interactive_graph(kind):
    result = render(pd.DataFrame({"x": [1.0], "fact": [25.0]}), ChartSpec(type=kind, x="x", y=["fact"]))
    assert result["status"] == "ready" and "data-mark" in result["svg"]
    assert len(result["metadata"]["tips"]) == 1
    assert "минимум две" not in result["notice"]


def test_one_heatmap_cell_becomes_a_column_with_month_and_outlet():
    result = render(
        pd.DataFrame({"period": ["2025-09"], "outlet": ["Атлас, Ставрополь"], "fact": [919018.41]}),
        ChartSpec(type="heatmap", x="period", group="outlet", y=["fact"]),
    )
    assert result["status"] == "ready" and result["metadata"]["min_width"] is None
    point = next(iter(result["metadata"]["tips"].values()))
    assert point["value"] == 919018.41
    assert "2025-09" in point["label"] and "Ставрополь" in point["label"]
    assert "919 018,41 ₽" in result["svg"]


@pytest.mark.parametrize("kind", ["bar", "line", "heatmap", "donut"])
def test_zero_is_a_valid_single_column_not_missing_data(kind):
    result = render(pd.DataFrame({"period": ["2025-09"], "fact": [0.0]}), ChartSpec(type=kind, y=["fact"]))
    assert result["status"] == "ready"
    assert next(iter(result["metadata"]["tips"].values()))["value"] == 0


def test_equal_heatmap_values_in_different_outlets_are_distinct_cells():
    result = render(
        pd.DataFrame({"period": ["2025-09", "2025-09"], "outlet": ["A", "B"], "fact": [10.0, 10.0]}),
        ChartSpec(type="heatmap", group="outlet", y=["fact"]),
    )
    assert result["metadata"]["min_width"] is not None
    assert len(result["metadata"]["tips"]) == 2


def test_constant_values_across_multiple_periods_remain_a_valid_trend():
    result = render(
        pd.DataFrame({"period": ["Jan", "Feb", "Mar"], "fact": [10.0, 10.0, 10.0]}),
        ChartSpec(type="line", y=["fact"]),
    )
    assert result["status"] == "ready" and len(result["metadata"]["tips"]) == 3


def test_heatmap_has_visible_color_for_constant_values_and_both_axes():
    from io import BytesIO
    from PIL import Image
    import numpy as np

    df = pd.DataFrame(
        {"period": ["2025-01", "2025-02"] * 2, "outlet": ["A", "A", "B", "B"], "fact": [10.0] * 4}
    )
    spec = ChartSpec(type="heatmap", source="table", x="period", group="outlet", y=["fact"])
    result = render(df, spec, output="png")
    pixels = np.array(Image.open(BytesIO(result["png"])).convert("RGB"))
    blue = (
        (pixels[:, :, 2] > pixels[:, :, 0] + 25)
        & (pixels[:, :, 2] > pixels[:, :, 1] + 10)
        & (pixels[:, :, 0] < 180)
    )
    assert blue.mean() > 0.05
    assert len(result["metadata"]["tips"]) == 4
    assert {v["label"] for v in result["metadata"]["tips"].values()} == {
        "A · 2025-01",
        "A · 2025-02",
        "B · 2025-01",
        "B · 2025-02",
    }


def test_legacy_all_hidden_series_are_visible_instead_of_blank():
    result = render(
        pd.DataFrame({"period": ["A", "B"], "fact": [10.0, 20.0]}), ChartSpec(y=["fact"], hidden=["fact"])
    )
    assert result["status"] == "ready" and len(result["metadata"]["tips"]) == 2
