"""Durable versions, scoped downloads, drafts, templates and public read-only snapshots."""

import csv
import io
import json
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from openpyxl import load_workbook
from conftest import build, post
from MY.public import create_public_app
from shared.storage import unpack
from shared.source import Source
from test_dashboard_cards import second_dashboard


def owner(client):
    return "web:" + client.cookies["puzzle_session"]


def mutate(client, path, args=None, message_id=None):
    return client.post(
        path,
        json={
            "revision": client.get("/api/state").json()["revision"],
            "arguments": args or {},
            "message_id": message_id,
        },
    )


def test_excel_and_csv_contain_all_computed_rows_for_the_selected_old_card(client):
    a = build(client)
    calculated = post(
        client,
        "/api/calculation",
        {"action": "add", "calculation": {"name": "gap", "label": "Разница", "expression": "fact-plan"}},
    ).json()
    aid = calculated["messages"][-1]["id"]
    second_dashboard(client)
    csv_data = client.get("/api/download/csv", params={"message_id": aid})
    assert csv_data.status_code == 200
    rows = list(csv.DictReader(io.StringIO(csv_data.content.decode("utf-8-sig"))))
    assert len(rows) == calculated["dataset"]["rows"] and "gap" in rows[0]
    assert float(rows[0]["gap"]) == pytest.approx(float(rows[0]["fact"]) - float(rows[0]["plan"]))
    excel = client.get("/api/download/xlsx", params={"message_id": aid})
    assert excel.status_code == 200
    book = load_workbook(io.BytesIO(excel.content), read_only=True, data_only=False)
    assert book.sheetnames == ["Данные", "Вычисления", "Параметры"]
    values = list(book["Данные"].values)
    assert "Разница" in values[0] and len(values) == len(rows) + 1
    assert list(book["Вычисления"].values)[1][3] == "fact-plan"
    assert client.get("/api/download/xlsx", params={"message_id": "missing"}).status_code == 400
    assert a["saved_id"] == calculated["saved_id"]


def test_png_is_a_single_dashboard_and_html_does_not_leak_conversation_or_raw_source(client):
    initial = build(client)
    store = client.app.state.store
    state = store.load(owner(client))
    state["messages"][0]["content"] = "PRIVATE_CONVERSATION_MARKER"
    store.commit(owner(client), state, state["revision"])
    png = client.get("/api/download/png")
    assert png.status_code == 200
    picture = Image.open(io.BytesIO(png.content))
    assert picture.format == "PNG" and picture.width == 2000 and picture.height > 1000
    page = client.get("/api/download/html")
    assert page.status_code == 200 and "PRIVATE_CONVERSATION_MARKER" not in page.text
    payload = json.loads(
        page.text.split('<script id="snapshot" type="application/json">')[1].split("</script>")[0]
    )
    assert len(payload["charts"]) == 4 and payload["kpis"] == initial["kpis"]
    assert set(payload) == {"title", "filters", "kpis", "period", "sync_zoom", "charts"}
    assert owner(client) not in page.text and "dataset" not in payload


def test_xlsx_untrusted_strings_are_not_excel_formulas(client):
    from shared.exports import xlsx_export

    build(client)
    state = client.app.state.store.load(owner(client))
    selected = next(row for row in state["dataset"]["rows"] if row["date"] >= state["filters"]["start"])
    selected["outlet"] = '=HYPERLINK("https://example.com")'
    state["snapshot"] = None
    book = load_workbook(io.BytesIO(xlsx_export(state)), data_only=False, read_only=True)
    first = list(book["Данные"].rows)[1]
    assert not any(c.data_type == "f" for c in first)
    assert any(str(c.value).startswith("'=HYPERLINK") for c in first)


def test_history_restore_is_new_version_and_restores_frozen_table_without_source(client, monkeypatch):
    initial = build(client)
    ident = initial["saved_id"]
    mid = initial["messages"][-1]["id"]
    assert len(client.get(f"/api/dashboards/{ident}/versions").json()["items"]) == 1
    changed = post(client, "/api/filters", {"regions": ["Центральный"]}).json()
    post(
        client,
        "/api/calculation",
        {"action": "add", "calculation": {"name": "gap", "label": "Разница", "expression": "fact-plan"}},
    )
    versions = client.get(f"/api/dashboards/{ident}/versions").json()["items"]
    assert len(versions) == 3

    def forbidden(*a, **k):
        raise AssertionError("Restoring a version cannot fetch new data")

    monkeypatch.setattr(Source, "load", forbidden)
    restored = mutate(client, f"/api/dashboards/{ident}/versions/1/restore", message_id=mid)
    assert restored.status_code == 200, restored.text
    restored = restored.json()
    assert restored["filters"] == initial["filters"] and restored["kpis"] == initial["kpis"]
    assert restored["calculations"] == [] and restored["saved_id"] == ident
    versions = client.get(f"/api/dashboards/{ident}/versions").json()["items"]
    assert len(versions) == 4 and versions[0]["label"] == "Восстановлена версия 1"
    assert client.app.state.store.version(owner(client), ident, 2)["filters"] == changed["filters"]


def test_restore_old_card_does_not_switch_next_dashboard_or_workspace(client):
    first = build(client)
    mid = first["messages"][-1]["id"]
    ident = first["saved_id"]
    second = second_dashboard(client)
    mutate(client, "/api/filters", {"regions": ["Центральный"]}, mid)
    restored = mutate(client, f"/api/dashboards/{ident}/versions/1/restore", message_id=mid).json()
    assert restored["dataset"]["id"] == second["dataset"]["id"]
    assert restored["filters"] == second["filters"] and restored["saved_id"] == second["saved_id"]
    assert next(m["dashboard"] for m in restored["messages"] if m["id"] == mid)["filters"] == first["filters"]


def test_versions_reuse_source_and_computed_table_blobs_for_zoom_only(client):
    first = build(client)
    post(client, "/api/chart", {"index": 1, "changes": {"zoom": [-0.4, 2.6, 0, 1e7]}})
    store = client.app.state.store
    with store.connect() as c:
        cores = [
            unpack(r["state"])
            for r in c.execute(
                "SELECT state FROM dashboard_versions WHERE owner=? AND dashboard_id=? ORDER BY seq",
                (owner(client), first["saved_id"]),
            )
        ]
    assert len(cores) == 2
    assert cores[0]["dataset_ref"] == cores[1]["dataset_ref"]
    assert cores[0]["snapshot_ref"] == cores[1]["snapshot_ref"]


def test_history_is_owner_scoped_and_rejects_stale_restore(client):
    initial = build(client)
    ident = initial["saved_id"]
    mid = initial["messages"][-1]["id"]
    with TestClient(client.app) as other:
        other.get("/")
        other.headers["X-Requested-With"] = "puzzle-rpa"
        assert other.get(f"/api/dashboards/{ident}/versions").status_code == 400
    assert (
        client.post(
            f"/api/dashboards/{ident}/versions/1/restore",
            json={"revision": 0, "arguments": {}, "message_id": mid},
        ).status_code
        == 409
    )
    assert mutate(client, f"/api/dashboards/{ident}/versions/999/restore", message_id=mid).status_code == 400


def test_drafts_are_per_context_owner_and_ignore_late_older_writes(client):
    first = client.post(
        "/api/draft", json={"context": "conversation", "text": "Новый черновик", "version": 30}
    ).json()
    client.post("/api/draft", json={"context": "conversation", "text": "Старый запрос", "version": 20})
    assert client.get("/api/draft").json()["text"] == "Новый черновик"
    assert (
        client.post("/api/draft", json={"context": "conversation", "text": "", "version": 40}).json()["text"]
        == ""
    )
    client.post("/api/draft", json={"context": "dashboard-a", "text": "Для A", "version": 1})
    assert client.get("/api/draft", params={"context": "dashboard-a"}).json()["text"] == "Для A"
    assert client.get("/api/draft").json()["text"] == ""
    with TestClient(client.app) as other:
        other.get("/")
        assert other.get("/api/draft").json()["text"] == ""
        assert other.get("/api/draft").json()["storage_key"] != first["storage_key"]


def test_custom_templates_are_persistent_owner_scoped_and_builtins_readonly(client):
    assert len(client.get("/api/templates").json()["items"]) == 3
    saved = client.post("/api/templates", json={"title": "Утро", "prompt": "Покажи продажи за 2025"}).json()
    client.patch("/api/templates/" + saved["id"], json={"title": "Вечер", "prompt": "Покажи регионы"})
    assert client.get("/api/templates").json()["items"][-1]["title"] == "Вечер"
    with TestClient(client.app) as other:
        other.get("/")
        other.headers["X-Requested-With"] = "puzzle-rpa"
        assert len(other.get("/api/templates").json()["items"]) == 3
        assert other.delete("/api/templates/" + saved["id"]).status_code == 400
    assert client.delete("/api/templates/sales").status_code == 400
    assert client.delete("/api/templates/" + saved["id"]).status_code == 200


def test_publishing_is_read_only_stable_until_updated_and_revocable(client, settings):
    settings.public_base_url = "https://dash.example"
    first = build(client)
    mid = first["messages"][-1]["id"]
    result = mutate(client, "/api/publications", message_id=mid)
    assert result.status_code == 200, result.text
    token = result.json()["published"]["token"]
    public = TestClient(create_public_app(settings))
    original = public.get("/s/" + token)
    assert original.status_code == 200
    assert (
        public.get("/api/state").status_code == 404
        and public.post("/api/chat/jobs", json={}).status_code == 404
    )
    assert public.get("/docs").status_code == 404
    post(client, "/api/filters", {"regions": ["Центральный"]})
    assert public.get("/s/" + token).text == original.text
    result = mutate(client, "/api/publications", message_id=mid).json()
    assert result["published"]["token"] == token
    assert public.get("/s/" + token).text != original.text
    client.delete("/api/publications/" + first["saved_id"])
    assert public.get("/s/" + token).status_code == 404


def test_deleted_dashboard_revokes_publication_and_history(client, settings):
    settings.public_base_url = "https://dash.example"
    first = build(client)
    token = mutate(client, "/api/publications", message_id=first["messages"][-1]["id"]).json()["published"][
        "token"
    ]
    client.delete("/api/dashboards/" + first["saved_id"])
    assert client.get("/api/dashboards/" + first["saved_id"] + "/versions").status_code == 400
    assert TestClient(create_public_app(settings)).get("/s/" + token).status_code == 404


def test_synchronized_zoom_links_time_axes_only_and_keeps_y_units_and_kpi(client):
    first = build(client)
    enabled = post(client, "/api/sync-zoom", {"enabled": True})
    assert enabled.status_code == 200
    zoom = [-0.4, 3.6, 0, 1e7]
    changed = post(client, "/api/chart", {"index": 1, "changes": {"zoom": zoom}}).json()
    assert changed["kpis"] == first["kpis"] and changed["filters"] == first["filters"]
    assert changed["charts"][3]["spec"]["zoom"][:2] == pytest.approx(zoom[:2])
    assert changed["charts"][3]["spec"]["zoom"][2:] != zoom[2:]
    assert all(changed["charts"][i]["spec"]["zoom"] is None for i in [1, 2])
    reset = post(client, "/api/chart", {"index": 1, "changes": {"zoom": None}}).json()
    assert all(c["spec"]["zoom"] is None for c in reset["charts"])


def test_sync_maps_heatmap_cell_centers_and_preserves_its_inverted_axis(client):
    build(client)
    post(
        client,
        "/api/chart",
        {
            "index": 2,
            "changes": {
                "type": "heatmap",
                "source": "table",
                "x": "period",
                "group": "region",
                "y": ["fact"],
            },
        },
    )
    post(client, "/api/sync-zoom", {"enabled": True})
    result = post(client, "/api/chart", {"index": 1, "changes": {"zoom": [-0.5, 2.5, 0, 1e7]}}).json()
    assert result["charts"][1]["spec"]["zoom"][:2] == [0, 3]
    assert (
        result["charts"][1]["metadata"]["axes"]["ylim"][0]
        > result["charts"][1]["metadata"]["axes"]["ylim"][1]
    )
