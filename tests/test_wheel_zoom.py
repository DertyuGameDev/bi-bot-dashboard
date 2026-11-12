"""Wheel viewports must redraw axes from the cached table, preserving card scope."""

import xml.etree.ElementTree as ET
import pandas as pd
import pytest
from conftest import build
from test_card_cache import edit
from test_dashboard_cards import second_dashboard
from shared.charts import render
from shared.models import ChartSpec
from shared.source import Source


@pytest.mark.parametrize("kind", ["line", "grouped_bar", "heatmap", "horizontal_bar"])
def test_zoom_metadata_and_preview_cover_painted_marks(kind):
    df = pd.DataFrame({"period": [f"2025-{i:02}" for i in range(1, 13)], "fact": range(12)})
    spec = ChartSpec(type=kind, x="period", y=["fact"], width=320)
    initial = render(df, spec)
    changed = render(df, spec.model_copy(update={"zoom": [3.6, 5.8, 1.2, 7.4]}))
    axes = changed["metadata"]["axes"]
    assert axes["xlim"] == [3.6, 5.8]
    assert sorted(axes["ylim"]) == [1.2, 7.4]
    assert (axes["ylim"][0] > axes["ylim"][1]) == (kind in {"heatmap", "horizontal_bar"})
    assert axes["full_xlim"] == initial["metadata"]["axes"]["xlim"]
    assert axes["full_ylim"] == initial["metadata"]["axes"]["ylim"]
    root = ET.fromstring(changed["svg"])
    marks = [el for el in root.iter() if el.get("data-mark")]
    assert marks and all(el.get("data-zoom-layer") for el in marks)
    if kind in {"line", "grouped_bar"}:
        # May was omitted at full scale on this narrow plot; zoom must restore it.
        ticks = [el.text for el in root.iter() if el.tag.endswith("}text")]
        assert "2025-05" in ticks and "2025-06" in ticks and "2025-01" not in ticks
    if kind == "heatmap":
        assert any(el.get("id", "").startswith("zoom_") and el.get("data-zoom-layer") for el in root.iter())


def test_old_card_zoom_redraws_and_persists_without_source_or_model(client, monkeypatch):
    first = build(client)
    ident = first["messages"][-1]["id"]
    second = second_dashboard(client)
    other = second["messages"][-1]
    untouched = client.get("/api/cards/" + other["id"]).json()
    monkeypatch.setattr(Source, "load", lambda *a, **k: pytest.fail("Zoom must use the saved table"))
    monkeypatch.setattr(
        client.app.state.chat_service.agent, "run", lambda *a, **k: pytest.fail("No AI for zoom")
    )
    response = edit(client, ident, {"index": 1, "changes": {"zoom": [1, 4, 0, 3e8]}}, "chart")
    assert response.status_code == 200, response.text
    card = response.json()["message"]["dashboard"]
    assert card["charts"][0]["metadata"]["axes"]["xlim"] == [1, 4]
    assert card["kpis"] == first["kpis"] and card["filters"] == first["filters"]
    restored = client.get("/api/cards/" + ident).json()["message"]["dashboard"]
    assert restored["charts"][0]["spec"]["zoom"] == [1, 4, 0, 3e8]
    after = client.get("/api/cards/" + other["id"]).json()
    assert after["settings_revision"] == untouched["settings_revision"]
    for key in ["kpis", "filters", "source_specs"]:
        assert after["message"]["dashboard"][key] == untouched["message"]["dashboard"][key]
