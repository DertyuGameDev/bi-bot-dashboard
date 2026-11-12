"""A common library per account; conversations and other accounts stay independent."""

import copy
import sqlite3
import pytest
from fastapi.testclient import TestClient
from conftest import build, post
from test_conversations import create, select
from test_card_cache import edit
from shared.source import Source
from shared.storage import Store, unpack
from shared.library_migration import migrate_shared_library


def mutation(client, path, arguments=None, message_id=None):
    result = client.post(
        path,
        json={
            "revision": client.get("/api/state").json()["revision"],
            "arguments": arguments or {},
            "message_id": message_id,
        },
    )
    assert result.status_code == 200, result.text
    return result.json()


def test_common_list_search_and_sort_without_merging_conversations(client):
    first = build(client)
    other = create(client)
    select(client, other)
    second = build(client)
    expected = {first["saved_id"], second["saved_id"]}
    assert {d["id"] for d in client.get("/api/dashboards").json()["items"]} == expected
    client.patch("/api/dashboards/" + first["saved_id"], json={"title": "Альфа", "as_new": False})
    client.patch("/api/dashboards/" + second["saved_id"], json={"title": "Январь", "as_new": False})
    b = client.get("/api/dashboards", params={"sort": "title"}).json()["items"]
    select(client, "main")
    assert client.get("/api/dashboards", params={"sort": "title"}).json()["items"] == b
    assert [d["title"] for d in b] == ["Альфа", "Январь"]
    assert len(client.get("/api/dashboards", params={"search": "АЛЬФА"}).json()["items"]) == 1
    assert len(client.get("/api/dashboards", params={"search": "последний год"}).json()["items"]) == 2
    assert [m["id"] for m in client.get("/api/state").json()["messages"]] == [
        m["id"] for m in first["messages"]
    ]


def test_cross_chat_open_restores_full_snapshot_and_autosaves_to_same_entry(client, monkeypatch):
    first = build(client)
    calculated = post(
        client,
        "/api/calculation",
        {"action": "add", "calculation": {"name": "gap", "label": "Разница", "expression": "fact-plan"}},
    ).json()
    post(client, "/api/filters", {"regions": ["Центральный"]})
    original = post(client, "/api/chart", {"index": 1, "changes": {"zoom": [1, 5, 0, 1e8]}}).json()
    store = client.app.state.store
    account = "web:" + client.cookies["puzzle_session"]
    saved_main = copy.deepcopy(store.load(account))
    other = create(client)
    select(client, other)
    before = build(client)
    monkeypatch.setattr(Source, "load", lambda *a, **k: pytest.fail("Opening must use the saved table"))
    opened = mutation(client, "/api/dashboards/" + first["saved_id"] + "/open")
    assert opened["saved_id"] == first["saved_id"] and opened["workspace"]["saved_id"] == first["saved_id"]
    for key in ["filters", "calculations", "kpis"]:
        assert opened[key] == original[key]
    assert opened["charts"][0]["spec"]["zoom"] == [1, 5, 0, 1e8]
    assert opened["dataset"]["id"] == calculated["dataset"]["id"]
    assert [m["id"] for m in opened["messages"][:-1]] == [m["id"] for m in before["messages"]]
    assert opened["messages"][-1]["id"] not in {m["id"] for m in original["messages"]}
    assert store.load(account) == saved_main
    changed = edit(client, opened["messages"][-1]["id"], {"regions": ["Уральский"]}).json()["message"][
        "dashboard"
    ]
    assert changed["saved_id"] == first["saved_id"] and changed["filters"]["regions"] == ["Уральский"]
    select(client, "main")
    restored = mutation(client, "/api/dashboards/" + first["saved_id"] + "/open")
    assert restored["filters"]["regions"] == ["Уральский"] and restored["calculations"][0]["name"] == "gap"
    assert len(client.get("/api/dashboards").json()["items"]) == 2
    with store.connect() as c:
        assert {r[0] for r in c.execute("SELECT DISTINCT owner FROM dashboards")} == {account}
        assert {r[0] for r in c.execute("SELECT DISTINCT owner FROM dashboard_blobs")} == {account}


def test_cross_chat_copy_has_own_entry_and_keeps_original_query(client):
    first = build(client)
    other = create(client)
    select(client, other)
    copied = mutation(client, "/api/dashboards/" + first["saved_id"] + "/open", {"as_new": True})
    assert copied["saved_id"] != first["saved_id"]
    post(client, "/api/filters", {"regions": ["Уральский"]})
    select(client, "main")
    items = client.get("/api/dashboards").json()["items"]
    assert len(items) == 2 and len({d["query"] for d in items}) == 1
    original = mutation(client, "/api/dashboards/" + first["saved_id"] + "/open")
    assert original["filters"]["regions"] == []
    own_copy = mutation(client, "/api/dashboards/" + copied["saved_id"] + "/open")
    assert own_copy["filters"]["regions"] == ["Уральский"]


def test_versions_and_publication_follow_dashboard_across_chats(client, monkeypatch):
    first = build(client)
    mid = first["messages"][-1]["id"]
    post(client, "/api/filters", {"regions": ["Центральный"]})
    client.app.state.settings.public_base_url = "https://viewer.example"
    published = mutation(client, "/api/publications", message_id=mid)["published"]
    other = create(client)
    select(client, other)
    versions = client.get(f"/api/dashboards/{first['saved_id']}/versions").json()["items"]
    assert len(versions) == 2
    monkeypatch.setattr(Source, "load", lambda *a, **k: pytest.fail("No source load"))
    opened = mutation(client, f"/api/dashboards/{first['saved_id']}/open")
    opened_id = opened["messages"][-1]["id"]
    actions = client.get("/api/card-actions", params={"message_id": opened_id}).json()
    assert actions["publication"]["published"]["token"] == published["token"]
    restored = mutation(
        client, f"/api/dashboards/{first['saved_id']}/versions/1/restore", message_id=opened_id
    )
    assert restored["filters"] == first["filters"] and restored["kpis"] == first["kpis"]
    assert len(client.get(f"/api/dashboards/{first['saved_id']}/versions").json()["items"]) == 3
    assert (
        mutation(client, "/api/publications", message_id=opened_id)["published"]["token"]
        == published["token"]
    )
    client.delete(f"/api/publications/{first['saved_id']}")
    select(client, "main")
    assert (
        client.get("/api/card-actions", params={"message_id": mid}).json()["publication"]["published"] is None
    )


def test_rename_and_delete_are_global_but_other_accounts_cannot_access(client):
    first = build(client)
    other_chat = create(client)
    select(client, other_chat)
    with TestClient(client.app) as stranger:
        stranger.get("/")
        stranger.headers["X-Requested-With"] = "puzzle-rpa"
        assert stranger.get("/api/dashboards").json()["items"] == []
        for suffix in ["versions"]:
            assert stranger.get(f"/api/dashboards/{first['saved_id']}/{suffix}").status_code == 400
        assert (
            stranger.post(
                f"/api/dashboards/{first['saved_id']}/open", json={"revision": 0, "arguments": {}}
            ).status_code
            == 400
        )
        assert (
            stranger.patch(
                f"/api/dashboards/{first['saved_id']}", json={"title": "Чужой", "as_new": False}
            ).status_code
            == 400
        )
        assert stranger.delete(f"/api/dashboards/{first['saved_id']}").status_code == 400
    assert (
        client.patch(
            f"/api/dashboards/{first['saved_id']}", json={"title": "Общий анализ", "as_new": False}
        ).status_code
        == 200
    )
    select(client, "main")
    assert client.get("/api/dashboards").json()["items"][0]["title"] == "Общий анализ"
    select(client, other_chat)
    assert client.delete(f"/api/dashboards/{first['saved_id']}").status_code == 200
    select(client, "main")
    assert client.get("/api/dashboards").json()["items"] == []
    assert client.get("/api/state").json()["messages"]


def legacy_library(client):
    """Rehome new-format fixtures into exactly the former per-chat namespaces."""
    first = build(client)
    account = "web:" + client.cookies["puzzle_session"]
    chat = create(client)
    select(client, chat)
    second = build(client)
    store = client.app.state.store
    scope = store.conversation_scope(account, chat)
    store.publish(scope, second["saved_id"], "Прежняя публикация", "<p>Frozen publication</p>")
    expected = {who: store.load(who) for who in [account, scope]}
    with store.connect() as c:
        c.execute("DELETE FROM storage_migrations WHERE name='account_library_v1'")
        for table, key in [
            ("dashboards", "id"),
            ("dashboard_versions", "dashboard_id"),
            ("publications", "dashboard_id"),
        ]:
            c.execute(f"UPDATE {table} SET owner=? WHERE {key}=?", (scope, second["saved_id"]))
        c.execute(
            "INSERT INTO dashboard_blobs SELECT ?,id,body FROM dashboard_blobs WHERE owner=?",
            (scope, account),
        )
        encoded = {
            who: unpack(c.execute("SELECT state FROM sessions WHERE owner=?", (who,)).fetchone()[0])
            for who in [account, scope]
        }

        def refs(who):
            return {
                encoded[who][key]["__data_blob__"] for key in ["dataset", "snapshot"] if encoded[who].get(key)
            }

        for ident in refs(scope) - refs(account):
            c.execute("DELETE FROM dashboard_blobs WHERE owner=? AND id=?", (account, ident))
    return store, account, scope, first, second, expected


def test_migration_is_idempotent_and_preserves_sessions_cards_versions_and_publication(client):
    store, account, scope, first, second, expected = legacy_library(client)
    reopened = Store(store.path)
    with reopened.connect() as c:
        assert (
            c.execute("SELECT owner FROM dashboards WHERE id=?", (second["saved_id"],)).fetchone()[0] == scope
        )
        before = {
            name: [tuple(r) for r in c.execute(f"SELECT * FROM {name} ORDER BY rowid")]
            for name in ["sessions", "dashboard_cards", "card_revisions"]
        }
        publication = c.execute("SELECT token,body FROM publications").fetchone()
    migrate_shared_library(reopened)
    migrate_shared_library(reopened)
    with reopened.connect() as c:
        for name, rows in before.items():
            assert [tuple(r) for r in c.execute(f"SELECT * FROM {name} ORDER BY rowid")] == rows
        assert tuple(c.execute("SELECT token,body FROM publications").fetchone()) == tuple(publication)
        assert (
            c.execute("SELECT COUNT(*) FROM storage_migrations WHERE name='account_library_v1'").fetchone()[0]
            == 1
        )
        for table in ["dashboards", "dashboard_versions", "dashboard_blobs", "publications"]:
            assert {r[0] for r in c.execute(f"SELECT DISTINCT owner FROM {table}")} == {account}
    assert reopened.list(account) == reopened.list(scope) and len(reopened.list(scope)) == 2
    for who in [account, scope]:
        assert reopened.load(who) == expected[who]
        assert reopened.open(who, second["saved_id"])["filters"] == second["filters"]
        assert reopened.versions(who, second["saved_id"])
        assert reopened.version(who, second["saved_id"], 1)["dataset"]
        assert reopened.publication(who, second["saved_id"])
        assert reopened.card(who, expected[who], expected[who]["messages"][-1]["id"])["dataset"]
    assert reopened.open(scope, first["saved_id"])["dataset"]["id"] == first["dataset"]["id"]


def test_failed_migration_rolls_back_and_can_be_retried(client):
    store, account, scope, first, second, expected = legacy_library(client)
    with store.connect() as c:
        before = {
            name: [tuple(r) for r in c.execute(f"SELECT * FROM {name} ORDER BY rowid")]
            for name in ["dashboards", "dashboard_versions", "dashboard_blobs", "publications"]
        }
        c.execute(
            "CREATE TRIGGER stop_library_migration BEFORE UPDATE ON dashboard_versions BEGIN SELECT RAISE(ABORT, 'simulated migration failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="simulated migration failure"):
        migrate_shared_library(store)
    with store.connect() as c:
        for name, rows in before.items():
            assert [tuple(r) for r in c.execute(f"SELECT * FROM {name} ORDER BY rowid")] == rows
        assert not c.execute("SELECT 1 FROM storage_migrations WHERE name='account_library_v1'").fetchone()
        c.execute("DROP TRIGGER stop_library_migration")
    migrate_shared_library(store)
    assert len(store.list(scope)) == 2 and store.load(scope) == expected[scope]
