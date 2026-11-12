"""Persistent slot filling precedes analytics and survives chat/workspace changes."""

import copy
import json
import threading
import pytest
from shared.agent import Agent
from shared.analytics import empty_state
from shared.chat import ChatService
from shared.dashboard_brief import (
    BriefPatch,
    BriefPeriod,
    KEY,
    apply_patch,
    missing,
    prepare,
    adopt_workspace,
    bind,
    sync_bound,
)
from shared.models import AgentStep
from shared.source import Source
from shared.storage import Store
from shared.tools import Tools
from shared.preflight_schema import Routing
from test_agent_loop import Scripted
from test_jobs import wait


def response(text="", *, patch=None, tool=None, args=None, intent="clarify", charts=None):
    return AgentStep(
        kind="act" if tool else "final",
        decision="Следующий шаг",
        calls=[{"tool": tool, "arguments": json.dumps(args or {})}] if tool else [],
        answer=text,
        intent=intent,
        charts=charts or [],
        table=False,
        exports=[],
        controls=[],
        brief_update=patch,
    )


@pytest.fixture
def brief_settings(settings):
    settings.dashboard_brief_enabled = True
    return settings


@pytest.mark.parametrize("explicit_save", [False, True])
def test_missing_period_is_saved_and_short_answer_resumes_original_goal(
    brief_settings, monkeypatch, explicit_save
):
    s = brief_settings
    store = Store(s.storage)
    chat = ChatService(s, store)
    state = empty_state()
    chat.agent.adapter = Scripted(
        lambda _: response(
            "За какой период сравнить выручку?",
            patch=BriefPatch(
                goal="Сравнить выручку по точкам", metric="amount", dimension="outlet", require_dimension=True
            ),
        )
    )
    original = Source.load
    monkeypatch.setattr(Source, "load", lambda *_: pytest.fail("No source access before clarification"))
    first = chat.process("qa", state, "Покажи сравнение выручки по точкам")
    store.commit("qa", state, 0)
    assert first["content"] == "За какой период сравнить выручку?" and not first.get("dashboard")
    assert missing(prepare(state)) == ["period"] and state["dataset"] is None
    assert store.list("qa") == []
    state = Store(s.storage).load("qa")
    state["messages"] = state["messages"][-1:]
    monkeypatch.setattr(Source, "load", original)

    def decide(payload):
        b = payload["context"][KEY]
        assert b["goal"] == "Сравнить выручку по точкам" and b["metric"] == "amount"
        observations = payload["observations"]
        if not observations:
            return response(
                tool="fetch_data",
                args={"start": "2025-01-01", "end": "2025-12-31"},
                patch=BriefPatch(period=BriefPeriod(mode="range", start="2025-01-01", end="2025-12-31")),
            )
        if len(observations) == 1:
            return response(tool="create_dashboard", args={"title": "Сравнение точек"})
        if len(observations) == 2 and explicit_save:
            return response(tool="save_dashboard", args={"title": "Сравнение точек"})
        return response("Сравнение готово.", intent="dashboard", charts=["*"])

    chat.agent.adapter = Scripted(decide)
    rev = state["revision"]
    reply = chat.process("qa", state, "За 2025 год")
    store.commit("qa", state, rev)
    assert reply.get("dashboard") and not missing(prepare(state))
    assert prepare(state).period.start == "2025-01-01"
    assert "Покажи сравнение выручки по точкам" in store.list("qa")[0]["query"]
    assert "За 2025 год" in store.list("qa")[0]["query"]


def test_model_cannot_skip_missing_fields_by_calling_fetch_defaults(brief_settings, monkeypatch):
    state = empty_state()
    agent = Agent(brief_settings, Store(brief_settings.storage))
    monkeypatch.setattr(Source, "load", lambda *_: pytest.fail("No implicit default dates"))

    def decide(payload):
        if not payload["observations"]:
            return response(tool="fetch_data")
        assert "period" in payload["observations"][-1]["error"]
        return response("За какой период?", patch=BriefPatch(goal="Обзор продаж", metric="amount"))

    agent.adapter = Scripted(decide)
    result = agent.run("qa", state, "Покажи продажи")
    assert result["text"] == "За какой период?" and state["dataset"] is None
    assert all(t["status"] == "error" for t in state["debug"]["tools"])


def test_incomplete_model_cannot_show_old_chart_instead_of_asking(brief_settings):
    state = empty_state()
    tools = Tools(brief_settings, Store(brief_settings.storage), "qa", state)
    tools.invoke("fetch_data", {"start": "2025-01-01", "end": "2025-12-31"})
    tools.invoke("create_dashboard", {})
    agent = Agent(brief_settings, Store(brief_settings.storage))

    def decide(payload):
        if not payload["observations"]:
            return response(
                "Готово",
                patch=BriefPatch(reset=True, goal="Новый анализ", metric="amount"),
                intent="dashboard",
                charts=["*"],
            )
        assert payload["observations"][-1]["tool"] == "dashboard_brief"
        return response("Какой период взять для нового анализа?")

    agent.adapter = Scripted(decide)
    result = agent.run("qa", state, "Новый анализ")
    assert not result["presentation"]["charts"] and missing(prepare(state)) == ["period"]


def test_greeting_preserves_pending_question_and_slots(brief_settings):
    state = empty_state()
    apply_patch(state, BriefPatch(goal="Сравнить продажи", metric="amount"), "Сравни продажи")
    state[KEY]["pending_question"] = "За какой период?"
    before = copy.deepcopy(state[KEY])
    agent = Agent(brief_settings, Store(brief_settings.storage))
    agent.adapter = Scripted(lambda _: response("Привет!", intent="text"))
    assert agent.run("qa", state, "Привет")["text"] == "Привет!"
    assert state[KEY] == before


def test_multi_turn_collection_and_explicit_all_time():
    state = empty_state()
    apply_patch(
        state, BriefPatch(goal="Сравнение продаж", metric="amount", require_dimension=True), "Сравни продажи"
    )
    assert missing(prepare(state)) == ["period", "dimension"]
    apply_patch(state, BriefPatch(period=BriefPeriod(mode="all_time")), "За всё время")
    assert missing(prepare(state)) == ["dimension"]
    apply_patch(state, BriefPatch(dimension="outlet"), "По торговым точкам")
    assert not missing(prepare(state)) and prepare(state).period.mode == "all_time"
    apply_patch(state, BriefPatch(clear_fields=["period"]), "Период ещё не определён")
    assert missing(prepare(state)) == ["period"]


@pytest.mark.parametrize(
    "value",
    [
        {"mode": "range", "start": "2025-02-30", "end": None},
        {"mode": "range", "start": "2025-02-01", "end": "2025-01-01"},
        {"mode": "all_time", "start": "2025-01-01", "end": None},
    ],
)
def test_invalid_periods_are_not_saved(value):
    with pytest.raises(ValueError):
        BriefPeriod.model_validate(value)


def test_slots_are_isolated_by_conversation_and_owner(brief_settings):
    store = Store(brief_settings.storage)
    a = store.new_conversation("alice")
    b = store.new_conversation("alice")
    for ident, year in [(a["id"], 2025), (b["id"], 2024)]:
        scope = store.conversation_scope("alice", ident)
        state = empty_state()
        apply_patch(
            state,
            BriefPatch(
                goal="Продажи",
                metric="amount",
                period=BriefPeriod(mode="range", start=f"{year}-01-01", end=f"{year}-12-31"),
            ),
            str(year),
        )
        store.commit(scope, state, 0)
    assert (
        prepare(Store(brief_settings.storage).load(store.conversation_scope("alice", a["id"]))).period.start
        == "2025-01-01"
    )
    assert prepare(store.load(store.conversation_scope("alice", b["id"]))).period.start == "2024-01-01"
    assert prepare(store.load("bob")).period is None
    with pytest.raises(ValueError):
        store.conversation_scope("bob", a["id"])


def test_selected_workspace_adopts_actual_parameters_and_new_analysis_clears_them(brief_settings):
    store = Store(brief_settings.storage)
    state = empty_state()
    tools = Tools(brief_settings, store, "qa", state)
    tools.invoke("fetch_data", {"start": "2025-01-01", "end": "2025-12-31"})
    tools.invoke("create_dashboard", {})
    state["workspace"] = {"query": "Длинный сохранённый запрос " * 150}
    adopt_workspace(state)
    assert not missing(prepare(state)) and prepare(state).period.start == "2025-01-01"
    assert len(prepare(state).goal) <= 1600 and state["debug"][KEY]["period"]["start"] == "2025-01-01"
    state["filters"].update(start="2025-09-01", end="2025-09-30", regions=["Центральный"])
    sync_bound(state)
    assert prepare(state).period.start == "2025-09-01" and prepare(state).regions == ["Центральный"]
    new = empty_state()
    adopt_workspace(new, state)
    assert prepare(new).period is None and prepare(new).revision > prepare(state).revision


def test_all_time_intent_survives_binding_but_ui_date_change_replaces_it(brief_settings):
    state = empty_state()
    tools = Tools(brief_settings, Store(brief_settings.storage), "qa", state)
    tools.invoke("fetch_data", {"start": "2025-01-01", "end": "2025-12-31"})
    apply_patch(
        state,
        BriefPatch(goal="Продажи", metric="amount", period=BriefPeriod(mode="all_time")),
        "За всё время",
    )
    bind(state)
    sync_bound(state)
    assert prepare(state).period.mode == "all_time"
    state["filters"].update(start="2025-09-01", end="2025-09-30")
    sync_bound(state)
    assert prepare(state).period.mode == "range" and prepare(state).period.start == "2025-09-01"


def test_new_analysis_during_job_keeps_new_brief(client):
    client.app.state.settings.dashboard_brief_enabled = True
    service = client.app.state.chat_service
    entered, release = threading.Event(), threading.Event()

    def decide(payload):
        entered.set()
        assert release.wait(5)
        return response("За какой период?", patch=BriefPatch(goal="Продажи", metric="amount"))

    service.agent.adapter = Scripted(decide)
    started = client.post(
        "/api/chat/jobs", json={"revision": 0, "message": "Покажи продажи", "request_id": "brief-race"}
    ).json()
    try:
        assert entered.wait(3)
        result = client.post("/api/workspace/new", json={"revision": 0, "arguments": {}})
        assert result.status_code == 200
    finally:
        release.set()
    assert wait(client, started["id"])["status"] == "done"
    owner = "web:" + client.cookies["puzzle_session"]
    brief = prepare(client.app.state.store.load(owner))
    assert brief.goal is None and brief.period is None and brief.pending_question is None


def test_advisory_preflight_status_cannot_break_a_valid_clarification():
    route = Routing(
        status="resolved",
        confidence="high",
        scope="new",
        question="За какой период?",
        actions=[{"kind": "dashboard.create", "chart_ids": [], "parameters": [], "missing": ["period"]}],
    )
    assert route.status == "needs_model" and route.confidence == "low"


def test_current_card_filters_sync_memory_without_exporting_internal_brief(client):
    from conftest import build
    from test_card_cache import edit
    from shared.exports import html_export

    view = build(client)
    client.app.state.settings.dashboard_brief_enabled = True
    store = client.app.state.store
    owner = "web:" + client.cookies["puzzle_session"]
    state = store.load(owner)
    adopt_workspace(state)
    state[KEY]["source_request"] = "PRIVATE_INTERNAL_BRIEF"
    store.commit(owner, state, state["revision"])
    ident = view["messages"][-1]["id"]
    result = edit(client, ident, {"regions": ["Центральный"], "start": "2025-09-01", "end": "2025-09-30"})
    assert result.status_code == 200, result.text
    assert result.json()["current"]["debug"][KEY]["period"]["start"] == "2025-09-01"
    state = store.load(owner)
    brief = prepare(state)
    assert brief.period.start == "2025-09-01" and brief.regions == ["Центральный"]
    assert brief.source_request == "PRIVATE_INTERNAL_BRIEF"
    page = html_export(state)
    assert "PRIVATE_INTERNAL_BRIEF" not in page and "pending_question" not in page


def test_slot_updates_do_not_invalidate_existing_visual_variants(brief_settings):
    store = Store(brief_settings.storage)
    state = empty_state()
    tools = Tools(brief_settings, store, "qa", state)
    tools.invoke("fetch_data", {"start": "2025-01-01", "end": "2025-12-31"})
    tools.invoke("create_dashboard", {})
    chat = ChatService(brief_settings, store)
    message = {"id": "same-card"}
    chat.dashboard("qa", state, message)
    before = chat.views.key(state, message, None)
    apply_patch(
        state,
        BriefPatch(goal="Другая аналитическая цель", clear_fields=["period"]),
        "Другой запрос без периода",
    )
    assert chat.views.key(state, message, None) == before
