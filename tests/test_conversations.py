"""Conversation routing must isolate every stateful analytics operation."""

import time
from fastapi.testclient import TestClient
from conftest import build, post
from shared.storage import Store


def create(client):
    response = client.post("/api/conversations", json={})
    assert response.status_code == 201
    return response.json()["id"]


def select(client, ident):
    client.headers["X-Conversation-ID"] = ident


def test_legacy_conversation_is_preserved_and_new_chat_is_empty(client):
    original = build(client)
    owner = "web:" + client.cookies["puzzle_session"]
    saved = client.app.state.store.load(owner)
    items = client.get("/api/conversations").json()["items"]
    assert len(items) == 1 and items[0]["id"] == "main" and items[0]["messages"] == 2
    ident = create(client)
    select(client, ident)
    empty = client.get("/api/state").json()
    assert empty["messages"] == [] and empty["dataset"] is None and empty["revision"] == 0
    assert [d["id"] for d in client.get("/api/dashboards").json()["items"]] == [original["saved_id"]]
    select(client, "main")
    restored = client.get("/api/state").json()
    assert [(m["id"], m["content"]) for m in restored["messages"]] == [
        (m["id"], m["content"]) for m in original["messages"]
    ]
    assert restored["saved_id"] == original["saved_id"]
    assert client.app.state.store.load(owner) == saved


def test_working_tables_belong_to_chat_but_saved_library_and_history_are_shared(client):
    first = build(client)
    ident = create(client)
    select(client, ident)
    second = build(client)
    post(client, "/api/filters", {"regions": ["Центральный"]})
    changed = post(
        client,
        "/api/calculation",
        {"action": "add", "calculation": {"name": "gap", "label": "Разница", "expression": "fact-plan"}},
    ).json()
    assert changed["calculations"] and changed["filters"]["regions"] == ["Центральный"]
    assert "gap" in client.get("/api/download/csv").text.splitlines()[0]
    assert (
        client.get("/api/card-actions", params={"message_id": first["messages"][-1]["id"]}).status_code == 400
    )
    assert client.get("/api/dashboards/" + first["saved_id"] + "/versions").status_code == 200
    assert len(client.get("/api/dashboards").json()["items"]) == 2
    select(client, "main")
    current = client.get("/api/state").json()
    assert current["filters"] == first["filters"] and current["calculations"] == []
    assert current["saved_id"] != second["saved_id"]
    assert "gap" not in client.get("/api/download/csv").text.splitlines()[0]


def test_drafts_are_independent_but_custom_templates_are_shared(client):
    client.post("/api/draft", json={"context": "conversation", "text": "Для основного чата", "version": 1})
    template = client.post("/api/templates", json={"title": "Обзор", "prompt": "Покажи продажи"}).json()
    ident = create(client)
    select(client, ident)
    assert client.get("/api/draft").json()["text"] == ""
    client.post("/api/draft", json={"context": "conversation", "text": "Для второго чата", "version": 1})
    assert client.get("/api/templates").json()["items"][-1]["id"] == template["id"]
    select(client, "main")
    assert client.get("/api/draft").json()["text"] == "Для основного чата"


def test_conversations_are_owner_scoped_and_manual_names_survive_next_message(client):
    ident = create(client)
    select(client, ident)
    build(client)
    items = client.get("/api/conversations").json()["items"]
    assert next(i for i in items if i["id"] == ident)["title"].startswith("Собери дашборд")
    assert client.patch("/api/conversations/" + ident, json={"title": "Мой анализ"}).status_code == 200
    revision = client.get("/api/state").json()["revision"]
    client.post("/api/chat", json={"message": "Привет", "revision": revision})
    assert (
        next(i for i in client.get("/api/conversations").json()["items"] if i["id"] == ident)["title"]
        == "Мой анализ"
    )
    with TestClient(client.app) as other:
        other.get("/")
        other.headers["X-Requested-With"] = "puzzle-rpa"
        other.headers["X-Conversation-ID"] = ident
        assert other.get("/api/state").status_code == 400
        assert other.patch("/api/conversations/" + ident, json={"title": "Чужой"}).status_code == 400
        assert len(other.get("/api/conversations").json()["items"]) == 1
    reopened = Store(client.app.state.settings.storage)
    assert reopened.conversation_scope("web:" + client.cookies["puzzle_session"], ident).endswith(
        ":chat:" + ident
    )


def test_background_job_keeps_its_chat_when_browser_switches(client, monkeypatch):
    ident = create(client)
    select(client, ident)
    original = client.app.state.chat_service.process

    def slow(*args, **kwargs):
        time.sleep(0.15)
        return original(*args, **kwargs)

    monkeypatch.setattr(client.app.state.chat_service, "process", slow)
    job = client.post(
        "/api/chat/jobs", json={"message": "Привет", "revision": 0, "request_id": "switch-chat"}
    ).json()
    select(client, "main")
    assert client.get("/api/chat/jobs/" + job["id"]).status_code == 400
    assert client.get("/api/chat/jobs/active").json()["job"] is None
    assert client.get("/api/state").json()["messages"] == []
    select(client, ident)
    for _ in range(100):
        result = client.get("/api/chat/jobs/" + job["id"]).json()
        if result["status"] in {"done", "error"}:
            break
        time.sleep(0.02)
    assert result["status"] == "done"
    assert len(client.get("/api/state").json()["messages"]) == 2
    select(client, "main")
    assert client.get("/api/state").json()["messages"] == []
