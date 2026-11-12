import json
import pytest
from shared.agent import Agent
from shared.chat import ChatService
from shared.models import AgentStep
from shared.storage import Store
from shared.model import ModelError
from shared.source import Source


def step(kind="act", tool=None, arguments=None, answer="", **kwargs):
    return AgentStep(
        kind=kind,
        decision="Наблюдаемый следующий шаг",
        calls=[{"tool": tool, "arguments": json.dumps(arguments or {})}] if tool else [],
        answer=answer,
        intent="text",
        charts=[],
        table=False,
        exports=[],
        controls=[],
        **kwargs,
    )


class Scripted:
    def __init__(self, decide):
        self.decide = decide
        self.payloads = []

    def step(self, prompt):
        payload = json.loads(prompt)
        self.payloads.append(payload)
        return self.decide(payload), {"model_ms": 1, "adapter": "scripted"}


def test_no_preflight_or_canned_answer_even_when_table_exists(settings, monkeypatch):
    from shared.analytics import empty_state, install_dataset

    state = empty_state()
    install_dataset(state, Source(settings).load("2025-01-01", "2025-12-31", "sales"))
    store = Store(settings.storage)
    chat = ChatService(settings, store)
    exact = "Привет! **Слушаю ваш вопрос.**\n\nБез автоматической сводки."
    adapter = Scripted(lambda _: step("final", answer=exact))
    chat.agent.adapter = adapter
    monkeypatch.setattr(Source, "schema", lambda *_: pytest.fail("no eager schema inspection"))
    monkeypatch.setattr(Source, "load", lambda *_: pytest.fail("no eager data fetch"))
    reply = chat.process("qa", state, "Привет")
    assert reply["content"] == exact
    assert not reply["attachments"] and not reply["buttons"]
    assert len(adapter.payloads) == 1
    assert adapter.payloads[0]["observations"] == []
    assert state["debug"]["tools"] == []


def test_react_uses_observations_recovers_from_tool_error_and_continues(settings):
    from shared.analytics import empty_state

    state = empty_state()
    store = Store(settings.storage)
    agent = Agent(settings, store)

    def decide(payload):
        observations = payload["observations"]
        if not observations:
            return step(tool="profile_table")  # Deliberate missing-data error.
        if len(observations) == 1:
            assert observations[-1]["status"] == "error" and "fetch_data" in observations[-1]["error"]
            return step(tool="fetch_data", arguments={"start": "2025-01-01", "end": "2025-12-31"})
        if len(observations) == 2:
            assert observations[-1]["status"] == "ok" and observations[-1]["result"]["rows"] > 0
            return step(tool="apply_filters", arguments={"filters": {"regions": ["Центральный"]}})
        if len(observations) == 3:
            return step(
                tool="analyze_table",
                arguments={"source": "period", "columns": ["fact", "plan", "completion"]},
            )
        rows = observations[-1]["result"]["rows"]
        assert len(rows) == 12
        return step("final", answer=f"По Центральному региону получено {len(rows)} месяцев.")

    adapter = Scripted(decide)
    agent.adapter = adapter
    result = agent.run("qa", state, "Разбери продажи Центрального региона")
    assert result["text"] == "По Центральному региону получено 12 месяцев."
    assert len(adapter.payloads) == 5
    assert state["filters"]["regions"] == ["Центральный"]
    assert [o["tool"] for o in adapter.payloads[-1]["observations"]] == [
        "profile_table",
        "fetch_data",
        "apply_filters",
        "analyze_table",
    ]
    assert state["debug"]["model"]["iterations"] == 5


def test_model_can_load_a_skill_then_answer_without_data(settings):
    from shared.analytics import empty_state

    state = empty_state()
    agent = Agent(settings, Store(settings.storage))

    def decide(payload):
        if not payload["observations"]:
            assert any(s["name"] == "formatting" for s in payload["skills"])
            return step(tool="load_skill", arguments={"name": "formatting"})
        assert "Инструменты" in payload["observations"][0]["result"]["instructions"]
        return step("final", answer="Ответ модели после чтения выбранного скилла.")

    agent.adapter = Scripted(decide)
    agent.run("qa", state, "Объясни формат отчёта")
    assert state["debug"]["skills"] == ["formatting"] and state["dataset"] is None


def test_loop_limit_is_a_real_error_without_fake_final_answer(settings):
    from shared.analytics import empty_state

    settings.agent_max_steps = 2
    state = empty_state()
    agent = Agent(settings, Store(settings.storage))
    agent.adapter = Scripted(lambda _: step(tool="not_a_tool"))
    with pytest.raises(ModelError, match="лимита"):
        agent.run("qa", state, "test")
    assert not any(m["role"] == "assistant" for m in state["messages"])


def test_agent_sees_repaired_saved_region_selection_without_preflight(settings):
    from shared.analytics import empty_state, install_dataset

    state = empty_state()
    install_dataset(state, Source(settings).load("2025-01-01", "2025-12-31", "sales"))
    first = state["dataset"]["rows"][0]
    other = next(r for r in state["dataset"]["rows"] if r["region"] != first["region"])
    state["filters"].update(regions=[first["region"]], outlets=[other["outlet"]])

    def decide(payload):
        assert payload["context"]["filters"]["regions"] == [first["region"]]
        assert payload["context"]["filters"]["outlets"] == []
        assert "других регионов" in payload["context"]["filter_notice"]
        assert payload["observations"] == []
        return step("final", answer="Ответ модели по согласованному выбору.")

    agent = Agent(settings, Store(settings.storage))
    agent.adapter = Scripted(decide)
    result = agent.run("qa", state, "Сравни точки по месяцам")
    assert result["text"] == "Ответ модели по согласованному выбору."
    assert state["debug"]["tools"] == []


@pytest.mark.parametrize(
    "invalid",
    [
        {"intent": "chart", "charts": []},
        {"intent": "dashboard", "charts": []},
        {"intent": "text", "charts": ["*"]},
        {"intent": "table", "table": False, "answer": "| Точка | Выручка |\n|---|---|\n| A | 25 |"},
    ],
)
def test_incomplete_visual_presentation_returns_to_react_instead_of_fake_completion(settings, invalid):
    from shared.analytics import empty_state, install_dataset

    state = empty_state()
    install_dataset(state, Source(settings).load("2025-01-01", "2025-12-31", "sales"))
    chat = ChatService(settings, Store(settings.storage))

    def decide(payload):
        observations = payload["observations"]
        if not observations:
            return step("final", answer="Ниже результат.").model_copy(update=invalid)
        if len(observations) == 1:
            assert observations[-1]["tool"] == "agent_protocol"
            assert observations[-1]["status"] == "error"
            return step(tool="create_dashboard")
        assert observations[-1]["result"]["charts"] == 4
        return step("final", answer="Показаны интерактивные графики.").model_copy(
            update={"intent": "dashboard", "charts": ["*"]}
        )

    chat.agent.adapter = Scripted(decide)
    reply = chat.process("qa", state, "Выдай выручку точек за 2025")
    assert reply["content"] == "Показаны интерактивные графики."
    assert len(reply["dashboard"]["charts"]) == 4
    assert all(c["svg"] for c in reply["dashboard"]["charts"])
    assert len(state["messages"]) == 2
    assert state["debug"]["model"]["iterations"] == 3


def test_all_time_fetch_replaces_empty_year_and_selects_entire_history(settings):
    from shared.analytics import empty_state, materialize
    from shared.tools import Tools

    state = empty_state()
    tools = Tools(settings, Store(settings.storage), "qa", state)
    tools.invoke("fetch_data", {"start": "2027-01-01", "end": "2027-12-31"})
    assert materialize(state)[0].empty
    result = tools.invoke("fetch_data", {"all_time": True})
    assert result["period"] == ["2024-01-01", "2025-12-31"]
    assert state["filters"]["start"] == "2024-01-01"
    assert state["filters"]["end"] == "2025-12-31"
    assert not materialize(state)[0].empty
