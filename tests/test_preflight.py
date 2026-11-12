import copy
import json
from pathlib import Path
import pytest
from shared.agent import Agent
from shared.analytics import empty_state, install_dataset
from shared.models import AgentStep, ToolCall
from shared.preflight import Routing, classify, local_route, routing_context
from shared.source import Source
from shared.storage import Store
from shared.tools import Tools
from scripts.benchmark_preflight import score

CORPUS = json.loads((Path(__file__).parent / "fixtures/preflight_cases.json").read_text())


@pytest.mark.parametrize("case", CORPUS["cases"], ids=lambda c: c["id"])
def test_intent_corpus(case):
    context = copy.deepcopy(CORPUS["context"])
    context["previous_requests"] = case.get("previous", [])
    before = copy.deepcopy(context)
    result = local_route(case["text"], context)
    assert score(case, result.model_dump())["full"], result
    assert context == before


@pytest.mark.parametrize(
    "text",
    [
        "Он написал «Удали первый график», а я хочу другое",
        "Удали первый график и измени второй график на линейный",
        "Поменяй фильтр",
    ],
)
def test_incomplete_rule_matches_defer_to_main_agent(text):
    assert local_route(text, CORPUS["context"]).status == "needs_model"


def loaded(settings):
    state = empty_state()
    install_dataset(state, Source(settings).load("2025-01-01", "2025-12-31", "sales"))
    Tools(settings, Store(settings.storage), "qa", state).invoke("create_dashboard", {})
    return state


def final(answer="Ответ основной модели без дополнений."):
    return AgentStep(
        kind="final",
        decision="Ответ",
        calls=[],
        answer=answer,
        intent="text",
        charts=[],
        table=False,
        exports=[],
        controls=[],
    )


def test_preflight_read_only_no_source_access(settings, monkeypatch):
    state = loaded(settings)
    state["dataset"]["rows"][0]["private_marker"] = "not-for-classifier"
    before = copy.deepcopy(state)
    monkeypatch.setattr(Source, "load", lambda *_: pytest.fail("No eager fetch"))
    monkeypatch.setattr(Source, "schema", lambda *_: pytest.fail("No eager schema"))
    route, telemetry = classify("Измени первый график на столбчатый", state)
    assert route.actions[0].chart_ids == [state["dashboard"]["charts"][0]["id"]]
    assert "private_marker" not in json.dumps(routing_context(state))
    assert telemetry["adapter"] == "local_rules"
    assert before == state


@pytest.mark.parametrize("failure", [False, True])
def test_hint_is_optional_and_answer_is_always_models(settings, failure):
    settings.preflight_mode = "model" if failure else "local"
    state = loaded(settings)
    before = copy.deepcopy(state["dashboard"])
    agent = Agent(settings, Store(settings.storage))

    class Adapter:
        def classify(self, _):
            raise ValueError("pretend sensitive provider failure")

        def step(self, prompt):
            payload = json.loads(prompt)
            assert ("preflight" in payload) != failure
            assert not payload["observations"]
            return final(), {"model_ms": 1}

    agent.adapter = Adapter()
    result = agent.run("qa", state, "Измени первый график на столбчатый")
    assert result["text"] == final().answer
    assert state["dashboard"] == before  # A hint never executes itself.
    assert not state["debug"]["tools"][-1:][0]["tool"] == "update_chart"
    assert "sensitive" not in json.dumps(state["debug"].get("preflight"))


def test_model_cannot_invent_target_ids(settings):
    state = loaded(settings)

    class Adapter:
        def classify(self, _):
            return Routing(
                status="resolved",
                confidence="high",
                scope="selected",
                question="",
                actions=[
                    {
                        "kind": "chart.remove",
                        "chart_ids": ["someone-elses-chart"],
                        "parameters": [],
                        "missing": [],
                    }
                ],
            ), {}

    with pytest.raises(ValueError, match="неизвестный график"):
        classify("Удали первый график", state, "model", Adapter())


def test_first_agent_step_contains_preflight_without_separate_model_call(settings):
    settings.preflight_mode = "agent"
    state = loaded(settings)
    agent = Agent(settings, Store(settings.storage))
    route, _ = classify("Измени первый график на столбчатый", state)

    class Adapter:
        count = 0

        def classify(self, _):
            pytest.fail("Fused mode must never call a separate classifier")

        def step(self, prompt):
            payload = json.loads(prompt)
            self.count += 1
            if self.count == 1:
                assert "context" in payload["preflight_instruction"]
                return final().model_copy(
                    update={
                        "kind": "act",
                        "answer": "",
                        "preflight": route,
                        "calls": [
                            ToolCall(tool="update_chart", arguments='{"index":1,"changes":{"type":"bar"}}')
                        ],
                    }
                ), {"model_ms": 10, "adapter": "scripted"}
            assert payload["preflight"]["hint"] == route.model_dump()
            assert payload["observations"][-1]["status"] == "ok"
            return final("Готово."), {"model_ms": 10, "adapter": "scripted"}

    agent.adapter = Adapter()
    result = agent.run("qa", state, "Измени первый график на столбчатый")
    assert result["text"] == "Готово."
    assert agent.adapter.count == 2  # Tool selection and final response; no classification roundtrip.
    assert state["dashboard"]["charts"][0]["type"] == "bar"
    assert state["debug"]["preflight"]["extra_model_calls"] == 0


def test_response_schema_requires_nullable_and_nested_fields():
    from shared.model import response_schema

    schema = response_schema(AgentStep)
    assert "preflight" in schema["required"]
    assert set(schema["$defs"]["ToolCall"]["required"]) == {"tool", "arguments"}
    assert schema["additionalProperties"] is False
    assert schema["properties"]["preflight"]["anyOf"][-1] == {"type": "null"}


def test_layout_removal_reorder_preserve_workspace(settings):
    state = loaded(settings)
    state["saved_id"] = "existing"
    state["workspace"] = {"saved_id": "existing"}
    before = copy.deepcopy(state)
    charts = state["dashboard"]["charts"]
    wanted = [charts[2]["id"], charts[0]["id"]]
    Tools(settings, Store(settings.storage), "qa", state).invoke(
        "update_dashboard", {"layout": "one-column", "chart_ids": wanted}
    )
    assert state["dashboard"]["id"] == before["dashboard"]["id"]
    assert state["dashboard"]["version"] == before["dashboard"]["version"] + 1
    assert state["dashboard"]["charts"] == [
        before["dashboard"]["charts"][2],
        before["dashboard"]["charts"][0],
    ]
    for key in ["dataset", "calculations", "filters", "saved_id", "workspace"]:
        assert state[key] == before[key]


@pytest.mark.parametrize("ids", [["unknown"], ["sales-period", "sales-period"]])
def test_layout_invalid_ids_are_atomic(settings, ids):
    state = loaded(settings)
    before = copy.deepcopy(state)
    with pytest.raises(ValueError):
        Tools(settings, Store(settings.storage), "qa", state).invoke(
            "update_dashboard", {"layout": "one-column", "chart_ids": ids}
        )
    assert state == before


def test_layout_is_rendered_and_cached_independently(settings):
    from shared.chat import ChatService
    from shared.exports import html_export
    from shared.charts.registry import RENDER_DASHBOARD

    state = loaded(settings)
    store = Store(settings.storage)
    chat = ChatService(settings, store)
    message = {"id": "layout-card"}
    token = RENDER_DASHBOARD.set((1100, 900))
    try:
        chat.dashboard("qa", state, message)
        wide = message["dashboard"]["charts"][0]["spec"]["width"]
        Tools(settings, store, "qa", state).invoke("update_dashboard", {"layout": "one-column"})
        chat.dashboard("qa", state, message)
        assert message["dashboard"]["layout"] == "one-column"
        assert not message["dashboard"]["cache_hit"]
        assert message["dashboard"]["charts"][0]["spec"]["width"] > wide
        assert "main{display:grid;grid-template-columns:minmax(0,1fr)" in html_export(state)
        chat.dashboard("qa", state, message)
        assert message["dashboard"]["cache_hit"]
        assert message["dashboard"]["layout"] == "one-column"
    finally:
        RENDER_DASHBOARD.reset(token)
