"""Independent updates, exact cache invalidation and concurrent analysis merges."""

import threading
import time
from fastapi.testclient import TestClient
from conftest import build
from test_dashboard_cards import second_dashboard
from test_jobs import wait
from shared.source import Source
from MY.app import create_app


def card(view, ident):
    return next(m["dashboard"] for m in view["messages"] if m["id"] == ident)


def edit(client, ident, args, operation="filters", revision=None):
    current = client.get("/api/cards/" + ident).json()
    return client.post(
        "/api/cards/" + ident + "/edit",
        json={
            "settings_revision": current["settings_revision"] if revision is None else revision,
            "operation": operation,
            "arguments": args,
        },
    )


def test_small_card_response_and_unrelated_revisions_do_not_block(client):
    first = build(client)
    a = first["messages"][-1]["id"]
    second = second_dashboard(client)
    b = second["messages"][-1]["id"]
    token = client.get("/api/cards/" + a).json()["settings_revision"]
    assert edit(client, b, {"regions": ["Уральский"]}).status_code == 200
    changed = edit(client, a, {"regions": ["Центральный"]}, revision=token)
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert "messages" not in body and body["current"] is None
    assert body["message"]["dashboard"]["filters"]["regions"] == ["Центральный"]
    assert card(client.get("/api/state").json(), b)["filters"]["regions"] == ["Уральский"]
    assert edit(client, a, {"regions": []}, revision=token).status_code == 409


def test_cached_variants_survive_restart_and_invalidate_on_source_or_calculation(
    client, settings, monkeypatch
):
    first = build(client)
    ident = first["messages"][-1]["id"]
    a = edit(client, ident, {"regions": ["Центральный"]}).json()["message"]["dashboard"]
    edit(client, ident, {"regions": []})
    again = edit(client, ident, {"regions": ["Центральный"]}).json()["message"]["dashboard"]
    assert again["cache_hit"] and again["kpis"] == a["kpis"] and again["charts"] == a["charts"]
    with TestClient(create_app(settings)) as restarted:
        restarted.cookies.update(client.cookies)
        restarted.headers.update(client.headers)
        edit(restarted, ident, {"regions": []})
        restored = edit(restarted, ident, {"regions": ["Центральный"]}).json()["message"]["dashboard"]
        assert restored["cache_hit"] and restored["kpis"] == a["kpis"]
        calculated = edit(
            restarted,
            ident,
            {"action": "add", "calculation": {"name": "gap", "label": "Разница", "expression": "fact-plan"}},
            "calculation",
        ).json()["message"]["dashboard"]
        assert not calculated["cache_hit"] and calculated["source_signature"] != a["source_signature"]
        assert calculated["calculation_names"] == ["gap"]
    # Even an external edit keeping the table id/version cannot reuse old source values.
    store = client.app.state.store
    owner = "web:" + client.cookies["puzzle_session"]
    state = store.load(owner)
    before = client.get("/api/cards/" + ident).json()["message"]["dashboard"]
    for row in state["dataset"]["rows"]:
        row["fact"] *= 2
    state["snapshot"] = None
    store.commit(owner, state, state["revision"])
    changed = client.get("/api/cards/" + ident).json()["message"]["dashboard"]
    assert not changed["cache_hit"] and changed["source_signature"] != before["source_signature"]
    assert changed["kpis"]["fact"] == before["kpis"]["fact"] * 2


def test_card_operations_and_owner_scope(client, monkeypatch):
    first = build(client)
    ident = first["messages"][-1]["id"]
    monkeypatch.setattr(
        Source, "load", lambda *a, **k: (_ for _ in ()).throw(AssertionError("No source fetch for UI edits"))
    )
    result = edit(client, ident, {"index": 1, "changes": {"hidden": ["plan"]}}, "chart")
    assert result.status_code == 200
    assert result.json()["message"]["dashboard"]["source_specs"][0]["hidden"] == ["plan"]
    assert edit(client, ident, {"enabled": True}, "sync").json()["message"]["dashboard"]["sync_zoom"]
    with TestClient(client.app) as other:
        other.get("/")
        other.headers["X-Requested-With"] = "puzzle-rpa"
        assert other.get("/api/cards/" + ident).status_code == 400
        assert (
            other.post(
                "/api/cards/" + ident + "/edit", json={"settings_revision": 0, "arguments": {}}
            ).status_code
            == 400
        )


def test_old_filter_finishes_before_model_and_survives_new_dashboard(client):
    first = build(client)
    ident = first["messages"][-1]["id"]
    entered, release = threading.Event(), threading.Event()
    service = client.app.state.chat_service
    original = service.agent.adapter.step

    def delayed(prompt):
        entered.set()
        assert release.wait(8)
        return original(prompt)

    service.agent.adapter.step = delayed
    job = client.post(
        "/api/chat/jobs",
        json={
            "message": "Собери следующий дашборд",
            "revision": first["revision"],
            "request_id": "parallel-old-filter",
        },
    ).json()
    try:
        assert entered.wait(3)
        start = time.perf_counter()
        changed = edit(client, ident, {"regions": ["Уральский"]})
        assert changed.status_code == 200, changed.text
        assert time.perf_counter() - start < 3
        assert client.get("/api/chat/jobs/" + job["id"]).json()["status"] == "running"
    finally:
        release.set()
    assert wait(client, job["id"])["status"] == "done"
    view = client.get("/api/state").json()
    assert card(view, ident)["filters"]["regions"] == ["Уральский"]
    assert view["messages"][-1]["id"] != ident and view["messages"][-1].get("dashboard")
    assert client.get("/api/cards/" + ident).json()["message"]["dashboard"]["filters"]["regions"] == [
        "Уральский"
    ]


def test_parallel_edit_of_selected_dashboard_forks_new_answer_without_losing_original(client):
    first = build(client)
    ident = first["messages"][-1]["id"]
    saved = first["saved_id"]
    current = client.post(
        "/api/workspace/continue", json={"revision": first["revision"], "arguments": {}, "message_id": ident}
    ).json()
    entered, release = threading.Event(), threading.Event()
    service = client.app.state.chat_service
    original = service.agent.adapter.step

    def delayed(prompt):
        entered.set()
        assert release.wait(8)
        return original(prompt)

    service.agent.adapter.step = delayed
    job = client.post(
        "/api/chat/jobs",
        json={
            "message": "Покажи только Центральный регион",
            "revision": current["revision"],
            "request_id": "parallel-working-filter",
        },
    ).json()
    try:
        assert entered.wait(3)
        assert edit(client, ident, {"regions": ["Уральский"]}).status_code == 200
    finally:
        release.set()
    assert wait(client, job["id"])["status"] == "done"
    view = client.get("/api/state").json()
    new = view["messages"][-1]["dashboard"]
    assert new["saved_id"] != saved
    assert new["filters"]["regions"] == ["Центральный"]
    assert card(view, ident)["filters"]["regions"] == ["Уральский"]
    owner = "web:" + client.cookies["puzzle_session"]
    assert client.app.state.store.open(owner, saved)["filters"]["regions"] == ["Уральский"]


def test_source_blobs_are_shared_and_legacy_card_fallback_hydrates_them(client):
    from shared.storage import unpack

    first = build(client)
    ident = first["messages"][-1]["id"]
    owner = "web:" + client.cookies["puzzle_session"]
    store = client.app.state.store
    with store.connect() as c:
        session = unpack(c.execute("SELECT state FROM sessions WHERE owner=?", (owner,)).fetchone()["state"])
        saved = unpack(
            c.execute(
                "SELECT state FROM dashboards WHERE owner=? AND id=?", (owner, first["saved_id"])
            ).fetchone()["state"]
        )
        scope = unpack(
            c.execute(
                "SELECT state FROM dashboard_cards WHERE owner=? AND message_id=?", (owner, ident)
            ).fetchone()["state"]
        )
        assert "rows" not in session["dataset"]
        assert (
            session["dataset"]["__data_blob__"]
            == saved["dataset"]["__data_blob__"]
            == scope["dataset"]["__data_blob__"]
        )
        c.execute("DELETE FROM dashboard_cards WHERE owner=? AND message_id=?", (owner, ident))
    result = client.get("/api/cards/" + ident)
    assert result.status_code == 200, result.text
    assert result.json()["message"]["dashboard"]["kpis"] == first["kpis"]
    assert len(store.load(owner)["dataset"]["rows"]) == first["dataset"]["loaded_rows"]


def test_failed_independent_save_rolls_back_blobs_history_and_settings(client, monkeypatch):
    first = build(client)
    ident = first["messages"][-1]["id"]
    owner = "web:" + client.cookies["puzzle_session"]
    store = client.app.state.store
    before = store.load(owner)
    token = store.card_revision(owner, ident)
    with store.connect() as c:
        count = c.execute("SELECT COUNT(*) FROM dashboard_blobs").fetchone()[0]
    original = store._save

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise ValueError("Сбой записи")

    monkeypatch.setattr(store, "_save", fail)
    failed = edit(
        client,
        ident,
        {"action": "add", "calculation": {"name": "gap", "label": "Разница", "expression": "fact-plan"}},
        "calculation",
    )
    assert failed.status_code == 400
    assert store.load(owner) == before and store.card_revision(owner, ident) == token
    with store.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM dashboard_blobs").fetchone()[0] == count
