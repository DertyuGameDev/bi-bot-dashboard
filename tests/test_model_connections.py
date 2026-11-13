"""Provider contracts, secret boundaries and a complete HTTP-adapter agent loop."""

import json
import stat
import os
import shutil
import subprocess
from pathlib import Path
import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from MY.app import create_app
from scripts.configure_model import configure, write_settings
from scripts.check_model import check
from shared.config import Settings
from shared.model import CodexAdapter, DemoAdapter, ModelError, model_adapter
from shared.model_api import APIAdapter
from shared.model_providers import PROVIDERS
from shared.models import AgentPlan, AgentStep


@pytest.fixture(autouse=True)
def isolated_model_credentials(monkeypatch):
    # Test runs must neither use nor print the installation owner's credentials.
    for name in list(os.environ):
        if name == "RPA_MODEL" or name.startswith("RPA_MODEL_") or name == "RPA_REASONING":
            monkeypatch.delenv(name)


def final():
    return AgentStep(
        kind="final",
        decision="Готово",
        calls=[],
        answer="Подключение работает",
        intent="text",
        charts=[],
        table=False,
        exports=[],
        controls=[],
    ).model_dump()


def api_settings(**kw):
    return Settings(
        _env_file=None,
        model_adapter="openai_compatible",
        model="chosen-model",
        model_base_url="https://model.example/v1",
        **kw,
    )


def response(protocol, result, **kw):
    if protocol == "responses":
        return {
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(result)}]}],
            **kw,
        }
    if protocol == "anthropic":
        return {
            "stop_reason": "tool_use",
            "content": [{"type": "tool_use", "name": "agent_response", "input": result}],
            **kw,
        }
    return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(result)}}], **kw}


@pytest.mark.parametrize("provider", [p for p in PROVIDERS if p not in {"codex", "demo"}])
def test_every_preset_uses_explicit_model_validates_schema_and_keeps_key_server_side(provider):
    seen = []
    s = Settings(_env_file=None, model_adapter=provider, model="my-model", model_api_key="private-test-key")
    if provider == "openai_compatible":
        s.model_base_url = "https://custom.example/v1"
    protocol = PROVIDERS[provider]["protocol"]

    def serve(req):
        body = json.loads(req.content)
        assert body["model"] == "my-model" and "private-test-key" not in req.content.decode()
        assert req.headers["authorization"] == "Bearer private-test-key"
        endpoint = {"responses": "/responses", "chat": "/chat/completions", "anthropic": "/messages"}[
            protocol
        ]
        assert req.url.path.endswith(endpoint)
        if protocol == "responses":
            assert body["text"]["format"]["type"] == "json_schema" and body["store"] is False
            assert "reasoning" not in body
        elif protocol == "anthropic":
            assert body["tool_choice"]["name"] == "agent_response"
            assert req.headers["anthropic-version"] == "2026-10-21"
        seen.append(body)
        return httpx.Response(200, json=response(protocol, final()))

    adapter = APIAdapter(s, transport=httpx.MockTransport(serve))
    output, metadata = adapter.step('{"request":"test"}')
    assert output.answer == "Подключение работает" and len(seen) == 1
    assert metadata["adapter"] == provider and "private-test-key" not in json.dumps(metadata)


@pytest.mark.parametrize("mode", ["json_schema", "json_object", "prompt"])
def test_explicit_compatible_formats_and_keyless_local_server(mode):
    s = api_settings(model_api_format=mode)

    def serve(req):
        body = json.loads(req.content)
        assert "authorization" not in req.headers
        if mode == "prompt":
            assert "response_format" not in body
            result = response("chat", final())
            result["choices"][0]["message"]["content"] = "```json\n" + json.dumps(final()) + "\n```"
        else:
            assert body["response_format"]["type"] == mode
            result = response("chat", final())
        return httpx.Response(200, json=result)

    assert APIAdapter(s, transport=httpx.MockTransport(serve)).step("test")[0].kind == "final"


@pytest.mark.parametrize("status", [301, 400, 401, 403, 404, 429, 500])
def test_errors_never_echo_credentials_or_provider_body_and_do_not_retry(status):
    calls = []

    def serve(req):
        calls.append(req)
        return httpx.Response(
            status,
            json={"error": "private-test-key echoed sensitive prompt"},
            headers={"Location": "https://other.example"},
        )

    adapter = APIAdapter(api_settings(model_api_key="private-test-key"), transport=httpx.MockTransport(serve))
    with pytest.raises(ModelError) as error:
        adapter.step("sensitive prompt")
    assert len(calls) == 1 and str(status) in str(error.value)
    assert "private-test-key" not in str(error.value) and "sensitive prompt" not in str(error.value)


@pytest.mark.parametrize(
    "bad",
    [
        {"choices": []},
        {"choices": [{"finish_reason": "length", "message": {"content": json.dumps(final())}}]},
        {"choices": [{"message": {"content": "not json"}}]},
        {"choices": [{"message": {"content": json.dumps(final() | {"kind": "shell"})}}]},
        {"choices": [{"message": {"content": json.dumps(final()), "tool_calls": [{"name": "shell"}]}}]},
        {"choices": [{"message": {"content": json.dumps(final()), "refusal": "no"}}]},
    ],
)
def test_invalid_truncated_refused_or_foreign_tool_output_is_not_executed(bad):
    adapter = APIAdapter(
        api_settings(), transport=httpx.MockTransport(lambda _: httpx.Response(200, json=bad))
    )
    with pytest.raises(ModelError, match="JSON"):
        adapter.step("test")


@pytest.mark.parametrize("protocol", ["responses", "anthropic"])
def test_native_incomplete_and_wrong_tool_are_rejected(protocol):
    s = api_settings()
    s.model_adapter = "openai" if protocol == "responses" else "anthropic"
    s.model_api_key = Settings(model_api_key="test", _env_file=None).model_api_key
    bad = response(protocol, final())
    if protocol == "responses":
        bad["status"] = "incomplete"
    else:
        bad["content"][0]["name"] = "shell"
    with pytest.raises(ModelError):
        APIAdapter(s, transport=httpx.MockTransport(lambda _: httpx.Response(200, json=bad))).step("test")


def test_timeout_and_invalid_json_are_actionable():
    def timeout(req):
        raise httpx.ReadTimeout("secret-url", request=req)

    with pytest.raises(ModelError, match="не ответила"):
        APIAdapter(api_settings(), transport=httpx.MockTransport(timeout)).step("test")
    with pytest.raises(ModelError, match="JSON"):
        APIAdapter(
            api_settings(), transport=httpx.MockTransport(lambda _: httpx.Response(200, text="bad"))
        ).step("test")


def test_custom_headers_reasoning_and_api_key_header():
    s = api_settings(
        model_api_key="test", model_api_headers='{"Authorization":"Custom secret","api-key":"extra"}'
    )
    s.model_adapter = "openai"
    s.model_api_reasoning = "low"

    def serve(req):
        assert req.headers["authorization"] == "Custom secret" and req.headers["api-key"] == "extra"
        assert json.loads(req.content)["reasoning"] == {"effort": "low"}
        return httpx.Response(200, json=response("responses", final()))

    APIAdapter(s, transport=httpx.MockTransport(serve)).step("test")


def test_custom_responses_gateway_can_be_keyless_and_rejects_foreign_tools():
    s = api_settings()
    s.model_adapter = "openai"

    def serve(req):
        assert "authorization" not in req.headers
        return httpx.Response(200, json=response("responses", final()))

    assert APIAdapter(s, transport=httpx.MockTransport(serve)).step("test")[0].kind == "final"
    bad = response("responses", final())
    bad["output"].append({"type": "function_call", "name": "shell"})
    with pytest.raises(ModelError):
        APIAdapter(s, transport=httpx.MockTransport(lambda _: httpx.Response(200, json=bad))).step("test")


def test_settings_and_health_do_not_reveal_secrets(settings):
    s = api_settings(model_api_key="private-test-key", model_api_headers='{"api-key":"other-private"}')
    assert "private-test-key" not in repr(s) and "other-private" not in repr(s)
    settings.model_adapter = "openai_compatible"
    settings.model_api_key = s.model_api_key
    settings.model_api_headers = s.model_api_headers
    with TestClient(create_app(settings)) as client:
        health = client.get("/api/health").json()
    assert health["provider"] == "Совместимый API" and health["reasoning"] is None
    assert "private" not in json.dumps(health)
    with pytest.raises(ValidationError) as e:
        api_settings(model_api_headers='{"x":"private-test-key\\n"}')
    assert "private-test-key" not in str(e.value)


@pytest.mark.parametrize(
    "url",
    ["file:///tmp/test", "https://user:password@host/v1", "https://host/v1?key=secret", "https://host/#x"],
)
def test_invalid_url_rejected_without_exposing_credential(url):
    with pytest.raises(ValidationError) as e:
        Settings(model_base_url=url, _env_file=None)
    assert url not in str(e.value)


def test_factory_and_missing_configuration():
    assert isinstance(model_adapter(Settings(_env_file=None)), CodexAdapter)
    assert isinstance(model_adapter(Settings(model_adapter="demo", _env_file=None)), DemoAdapter)
    assert isinstance(model_adapter(api_settings()), APIAdapter)
    s = api_settings()
    s.model_base_url = ""
    with pytest.raises(ModelError, match="Base URL"):
        model_adapter(s).step("test")
    s.model_adapter = "openai"
    with pytest.raises(ModelError, match="API-ключ"):
        model_adapter(s).step("test")


def test_env_write_preserves_database_and_literal_secrets(tmp_path):
    from dotenv import dotenv_values

    path = tmp_path / ".env"
    path.write_text("# keep\nRPA_DATABASE_URL=private-db\nRPA_MODEL=old\nRPA_MODEL=duplicate\n")
    secret = "test-${NOT_A_VAR}-quote'back\\slash"
    write_settings(path, {"RPA_MODEL": "chosen", "RPA_MODEL_API_KEY": secret})
    values = dotenv_values(path)
    assert values["RPA_DATABASE_URL"] == "private-db" and values["RPA_MODEL"] == "chosen"
    assert dotenv_values(path, interpolate=False)["RPA_MODEL_API_KEY"] == secret
    assert Settings(_env_file=path).model_api_key.get_secret_value() == secret
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.read_text().count("RPA_MODEL=") == 1
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(ValueError):
        write_settings(link, {"RPA_MODEL": "other"})


def test_wizard_does_not_reuse_old_provider_key_and_adapts_host_for_docker(tmp_path, capsys):
    path = tmp_path / ".env.docker"
    path.write_text("BI_DB_ADMIN_PASSWORD=private-db\nBI_MODEL_API_KEY=old-secret\n")
    answers = iter(["ollama", "local-model", "", "", "нет"])
    values = configure(path, docker=True, read=lambda _: next(answers), secret=lambda _: "")
    assert values["BI_MODEL_BASE_URL"] == "http://host.docker.internal:11434/v1"
    assert values["BI_MODEL_API_KEY"] == "" and "old-secret" not in path.read_text()
    assert "BI_DB_ADMIN_PASSWORD=private-db" in path.read_text()
    assert "private-db" not in capsys.readouterr().out


def test_check_requires_a_completed_turn(monkeypatch):
    import scripts.check_model as module

    class Adapter:
        def step(self, _):
            return AgentStep.model_validate(final()), {"model_ms": 1}

    monkeypatch.setattr(module, "model_adapter", lambda _: Adapter())
    assert check(api_settings())["real_model"] is True
    assert check(Settings(model_adapter="demo", _env_file=None))["real_model"] is False


def test_api_agent_react_loop_builds_dashboard_and_table_then_restores(settings):
    settings.model_adapter = "openai_compatible"
    settings.model = "chosen-model"
    settings.model_base_url = "https://custom.example/v1"
    seen = []

    def serve(req):
        request = json.loads(req.content)
        payload = json.loads(request["messages"][1]["content"])
        seen.append(payload)
        observations = payload["observations"]
        out = final()
        if not observations:
            out.update(
                kind="act",
                answer="",
                calls=[
                    {
                        "tool": "fetch_data",
                        "arguments": json.dumps({"start": "2025-01-01", "end": "2025-12-31"}),
                    }
                ],
            )
        elif len(observations) == 1:
            assert observations[0]["status"] == "ok"
            out.update(kind="act", answer="", calls=[{"tool": "create_dashboard", "arguments": "{}"}])
        else:
            assert observations[-1]["status"] == "ok"
            out.update(intent="dashboard", charts=["*"], table=True)
        return httpx.Response(200, json=response("chat", out))

    with TestClient(create_app(settings)) as client:
        client.get("/")
        client.headers["X-Requested-With"] = "puzzle-rpa"
        client.app.state.chat_service.agent.adapter = APIAdapter(
            settings, transport=httpx.MockTransport(serve)
        )
        rev = client.get("/api/state").json()["revision"]
        result = client.post(
            "/api/chat", json={"revision": rev, "message": "Собери дашборд за 2025 и покажи таблицу"}
        )
        assert result.status_code == 200, result.text
        board = result.json()
        assert len(board["dashboard"]["charts"]) == 4 and board["saved_id"]
        cookies = client.cookies
    assert len(seen) == 3
    with TestClient(create_app(settings)) as restarted:
        restarted.cookies.update(cookies)
        restored = restarted.get("/api/state").json()
        assert restored["saved_id"] == board["saved_id"] and restored["kpis"] == board["kpis"]


def test_api_plan_uses_plan_schema():
    plan = {"intent": "text", "clarification": "", "calls": []}

    def serve(req):
        schema = json.loads(req.content)["messages"][0]["content"]
        assert "AgentPlan" in schema
        return httpx.Response(200, json=response("chat", plan))

    output, _ = APIAdapter(api_settings(), transport=httpx.MockTransport(serve)).plan("test")
    assert isinstance(output, AgentPlan)


@pytest.mark.skipif(not shutil.which("docker"), reason="Docker Compose CLI unavailable")
def test_compose_preserves_literal_model_headers_and_key(tmp_path):
    root = Path(__file__).resolve().parents[1]
    path = tmp_path / "private.env"
    path.write_text((root / ".env.docker.example").read_text())
    secret = "fake-test-${NOT_A_VAR}-quote'back\\slash"
    headers = json.dumps({"api-key": secret})
    write_settings(
        path,
        {
            "BI_MODEL_API_HEADERS": headers,
            "BI_MODEL_API_KEY": secret,
            "BI_MODEL_ADAPTER": "openai_compatible",
            "BI_MODEL": "test",
        },
    )
    environment = {k: v for k, v in os.environ.items() if not k.startswith("BI_")}
    config = json.loads(
        subprocess.check_output(
            ["docker", "compose", "--env-file", str(path), "config", "--format", "json"],
            cwd=root,
            env=environment,
        )
    )
    app = config["services"]["app"]["environment"]
    # Compose escapes dollars when serializing its resolved config for reuse.
    assert app["RPA_MODEL_API_HEADERS"].replace("$$", "$") == headers
    assert app["RPA_MODEL_API_KEY"].replace("$$", "$") == secret
    # The viewer receives neither credentials nor connection settings.
    assert not any("MODEL_API" in key for key in config["services"]["viewer"]["environment"])
