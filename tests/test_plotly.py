"""All interactive chart types use native, valid Plotly traces from the selected data."""

import json
import pandas as pd
import plotly.graph_objects as go
import pytest
from shared.charts import render
from shared.models import ChartSpec
from shared.storage import unpack
from conftest import build


@pytest.mark.parametrize(
    "kind",
    [
        "bar",
        "grouped_bar",
        "stacked_bar",
        "horizontal_bar",
        "line",
        "scatter",
        "histogram",
        "heatmap",
        "donut",
    ],
)
def test_all_chart_types_have_valid_native_plotly_figures(kind):
    frame = pd.DataFrame(
        {"period": ["2025-01", "2025-02", "2025-03"], "plan": [10.0, 20.0, 30.0], "fact": [8.0, 22.0, 28.0]}
    )
    x = "plan" if kind == "scatter" else "period"
    spec = ChartSpec(
        type=kind, x=x, y=["fact"] if kind in {"donut", "heatmap", "histogram"} else ["plan", "fact"]
    )
    result = render(frame, spec)
    f = result["plotly"]
    go.Figure(data=f["data"], layout=f["layout"])  # Reject invalid schema, axes, arrays or trace properties.
    json.dumps(f, allow_nan=False)
    assert f["data"] and f["meta"]["cartesian"] == (kind != "donut")
    assert all(trace["meta"]["key"] in spec.y for trace in f["data"])
    if kind == "stacked_bar":
        assert f["layout"]["barmode"] == "stack"
    if kind in {"heatmap", "horizontal_bar"}:
        assert f["layout"]["yaxis"]["range"][0] > f["layout"]["yaxis"]["range"][1]
    if kind == "donut":
        assert f["data"][0]["type"] == "pie" and f["layout"]["showlegend"]


def test_heatmap_offset_maps_legacy_saved_ranges_without_changing_values():
    frame = pd.DataFrame(
        {"period": ["2025-01", "2025-02"] * 2, "region": ["A", "A", "B", "B"], "fact": [1.0, 2.0, 3.0, 4.0]}
    )
    spec = ChartSpec(type="heatmap", x="period", group="region", y=["fact"], zoom=[0.25, 1.75, 0.1, 1.9])
    f = render(frame, spec)["plotly"]
    assert f["meta"]["offset"] == [0.5, 0.5]
    assert f["layout"]["xaxis"]["range"] == [-0.25, 1.25]
    assert f["layout"]["yaxis"]["range"] == [1.4, -0.4]
    assert f["data"][0]["z"] == [[1, 2], [3, 4]]


def test_plotly_series_hidden_and_untrusted_labels_are_not_html():
    f = render(
        pd.DataFrame(
            {"group": ["<img src=x onerror=alert(1)>", "Safe"], "plan": [20.0, 30.0], "fact": [21.0, 29.0]}
        ),
        ChartSpec(x="group", hidden=["plan"]),
    )["plotly"]
    assert f["data"][0]["visible"] == "legendonly" and f["data"][1]["visible"] is True
    assert f["data"][0]["x"][0].startswith("&lt;img")
    assert "<img" not in json.dumps(f)


def test_scatter_rejects_pandas_string_axes_as_validation_error():
    with pytest.raises(ValueError, match="должна быть числовой"):
        render(
            pd.DataFrame({"period": ["2025-01", "2025-02"], "fact": [1.0, 2.0]}),
            ChartSpec(type="scatter", x="period", y=["fact"]),
        )


@pytest.mark.parametrize("kind", ["histogram", "horizontal_bar"])
def test_numeric_x_axes_are_not_calendar_linked(kind):
    f = render(
        pd.DataFrame({"period": ["2025-01", "2025-02"], "fact": [1.0, 2.0]}),
        ChartSpec(type=kind, x="period", y=["fact"]),
    )["plotly"]
    assert f["meta"]["periods"] == []


def test_scatter_can_compare_numeric_monthly_aggregates_without_loading_raw_rows(client):
    from conftest import post

    build(client)
    changed = post(
        client,
        "/api/chart",
        {
            "index": 1,
            "changes": {"type": "scatter", "source": "period", "x": "plan", "y": ["fact"], "zoom": None},
        },
    )
    assert changed.status_code == 200, changed.text
    chart = changed.json()["charts"][0]
    assert chart["spec"]["x"] == "plan" and len(chart["plotly"]["data"][0]["x"]) == 12
    assert all(isinstance(v, (float, int)) for v in chart["plotly"]["data"][0]["x"])


def test_changing_axes_resets_old_viewport_but_title_edit_preserves_it(client):
    from conftest import post

    build(client)
    zoom = [1, 4, 0, 1e8]
    post(client, "/api/chart", {"index": 1, "changes": {"zoom": zoom}})
    title = post(client, "/api/chart", {"index": 1, "changes": {"title": "Новый заголовок"}}).json()
    assert title["charts"][0]["spec"]["zoom"] == zoom
    changed = post(
        client, "/api/chart", {"index": 1, "changes": {"type": "scatter", "x": "plan", "y": ["fact"]}}
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["charts"][0]["spec"]["zoom"] is None


def test_rebuilding_interactive_figures_preserves_current_card_revision(client):
    initial = build(client)
    ident = initial["messages"][-1]["id"]
    expected = client.get("/api/cards/" + ident).json()["settings_revision"]
    client.app.state.chat_service.views.engine = "test-plotly-new-engine"
    refreshed = client.get("/api/state").json()
    token = refreshed["messages"][-1]["dashboard"]["settings_revision"]
    assert token == expected
    edited = client.post(
        "/api/cards/" + ident + "/edit",
        json={
            "settings_revision": token,
            "operation": "chart",
            "arguments": {"index": 1, "changes": {"zoom": [1, 4, 0, 1e8]}},
        },
    )
    assert edited.status_code == 200, edited.text


def test_html_embeds_plotly_and_russian_locale_without_mutating_saved_state(client):
    initial = build(client)
    owner = "web:" + client.cookies["puzzle_session"]
    store = client.app.state.store
    with store.connect() as c:
        before = bytes(c.execute("SELECT state FROM sessions WHERE owner=?", (owner,)).fetchone()[0])
    html = client.get("/api/download/html").text
    assert "plotly.js v4." in html and "chartWidget" in html and "plotly_relayout" in html
    assert 'src="https://cdn.plot.ly' not in html
    payload = json.loads(
        html.split('<script id="snapshot" type="application/json">')[1].split("</script>")[0]
    )
    assert all(c["plotly"]["data"] for c in payload["charts"]) and payload["kpis"] == initial["kpis"]
    assert set(payload) == {"title", "filters", "kpis", "period", "sync_zoom", "charts"}
    with store.connect() as c:
        after = bytes(c.execute("SELECT state FROM sessions WHERE owner=?", (owner,)).fetchone()[0])
    assert unpack(before) == unpack(after)
