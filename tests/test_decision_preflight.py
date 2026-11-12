import copy
import json
import math
from pathlib import Path
import httpx
import pytest
from shared.config import Settings
from shared.decision_preflight import decode, decision_route, questions
from shared.preflight import classify
from shared.agent import Agent
from shared.storage import Store
from test_preflight import loaded, final

CONTEXT = json.loads((Path(__file__).parent / "fixtures/preflight_cases.json").read_text())["context"]


def answers(q, kinds=("chart.modify",), targets=("c1",), parameters=None):
    params = {"type": "bar", **(parameters or {})}
    result = {}
    for key, spec in q.items():
        if spec["type"] == "noul":
            on = key in {"intent:" + k for k in kinds} | {"target:" + i for i in targets}
            result[key] = {"type": "noul", "noul": 0.98 if on else 0.02}
        else:
            value = params.get(key.split(":", 1)[1], "unchanged")
            result[key] = {
                "type": "choice",
                "choice": value,
                "probabilities": {k: float(k == value) for k in spec["criteria"]},
            }
    return {"model": "kev-latest", "answers": result, "latency_ms": 123}


def test_decisions_support_multiple_intents_without_generating_a_plan():
    q = questions(CONTEXT)
    result = answers(
        q, kinds=["chart.modify", "calculation.add", "data.filter"], parameters={"granularity": "quarter"}
    )
    route, probabilities = decode(
        result,
        q,
        "Первый график сделай столбчатым, добавь столбец fact - plan и переключи период на кварталы",
        CONTEXT,
    )
    assert {a.kind for a in route.actions} == {"chart.modify", "calculation.add", "data.filter"}
    chart = next(a for a in route.actions if a.kind == "chart.modify")
    assert chart.chart_ids == ["c1"] and chart.parameters[0].value == "bar"
    assert next(a for a in route.actions if a.kind == "calculation.add").parameters[0].value == "fact - plan"
    assert probabilities["intent:chart.modify"]["true"] == 0.98


@pytest.mark.parametrize("value", [math.nan, math.inf, -0.1, 1.1, True, "0.8"])
def test_invalid_probabilities_are_rejected(value):
    q = questions(CONTEXT)
    body = answers(q)
    body["answers"]["intent:chart.modify"]["noul"] = value
    with pytest.raises(ValueError, match="вероятность"):
        decode(body, q, "Измени график", CONTEXT)


def test_foreign_option_and_incomplete_answers_are_rejected():
    q = questions(CONTEXT)
    body = answers(q)
    body["answers"]["parameter:type"]["probabilities"] = {"foreign": 1.0}
    with pytest.raises(ValueError, match="распределение"):
        decode(body, q, "Измени график", CONTEXT)
    del body["answers"]["target:c1"]
    with pytest.raises(ValueError, match="неполный"):
        decode(body, q, "Измени график", CONTEXT)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://192.168.1.1:8009",
        "http://user:password@localhost:8009",
        "http://localhost:8009/wrong",
        "http://localhost:8009?key=abc",
    ],
)
def test_endpoint_is_local_only(url):
    with pytest.raises(ValueError, match="loopback"):
        Settings(decision_url=url, _env_file=None)


def test_real_http_contract_has_no_source_rows_and_does_not_mutate_state(settings, monkeypatch):
    state = loaded(settings)
    state["dataset"]["rows"][0]["secret"] = "private-source-value"
    before = copy.deepcopy(state)
    client = httpx.Client
    seen = []

    def handle(request):
        payload = json.loads(request.content)
        seen.append(payload)
        assert request.url == "http://127.0.0.1:8009/v1/systemone"
        assert "private-source-value" not in request.content.decode()
        first = state["dashboard"]["charts"][0]["id"]
        return httpx.Response(200, json=answers(payload["questions"], targets=[first]))

    monkeypatch.setattr(httpx, "Client", lambda **kw: client(transport=httpx.MockTransport(handle), **kw))
    route, meta = classify("Первый график сделай столбчатым", state, "decision", settings=settings)
    assert len(seen) == 1 and route.actions[0].chart_ids == [state["dashboard"]["charts"][0]["id"]]
    assert meta["adapter"] == "local_decision_model" and meta["calibrated_on_this_task"] is False
    assert state == before


def test_decision_service_failure_keeps_main_react_available(settings, monkeypatch):
    settings.preflight_mode = "decision"
    state = loaded(settings)
    before = copy.deepcopy(state["dashboard"])

    def fail(*args, **kwargs):
        raise ValueError("Unavailable")

    monkeypatch.setattr("shared.decision_preflight.decision_route", fail)

    class Adapter:
        def step(self, prompt):
            assert "preflight" not in json.loads(prompt)
            return final("Основной агент продолжил ответ."), {"model_ms": 1}

    agent = Agent(settings, Store(settings.storage))
    agent.adapter = Adapter()
    assert agent.run("qa", state, "Привет")["text"] == "Основной агент продолжил ответ."
    assert state["dashboard"] == before and state["debug"]["preflight"]["status"] == "unavailable"


def test_remote_failure_and_empty_intent_are_not_invented(monkeypatch):
    client = httpx.Client

    def handle(request):
        return httpx.Response(503)

    monkeypatch.setattr(httpx, "Client", lambda **kw: client(transport=httpx.MockTransport(handle), **kw))
    with pytest.raises(ValueError, match="недоступна"):
        decision_route("Привет", CONTEXT)
    q = questions(CONTEXT)
    route, _ = decode(
        answers(q, kinds=[], targets=[], parameters={"type": "unchanged"}), q, "Непонятно", CONTEXT
    )
    assert route.status == "needs_model" and route.actions[0].kind == "unknown"
