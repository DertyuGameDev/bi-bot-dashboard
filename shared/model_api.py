"""HTTP transports for the same validated ReAct protocol used by Codex."""

import json
import time
import httpx
from shared.model import ModelError, response_schema
from shared.models import AgentPlan, AgentStep
from shared.model_providers import PROVIDERS


class APIAdapter:
    def __init__(self, settings, *, transport=None):
        self.settings = settings
        self.transport = transport
        self.preset = PROVIDERS[settings.model_adapter]

    def step(self, prompt):
        return self._generate(prompt, AgentStep)

    def plan(self, prompt):
        return self._generate(prompt, AgentPlan)

    def classify(self, prompt):
        from shared.preflight import Routing

        return self._generate(prompt, Routing)

    def _generate(self, prompt, response_type):
        s = self.settings
        base = s.model_base_url or self.preset["url"]
        key = s.model_api_key.get_secret_value()
        extra_headers = json.loads(s.model_api_headers.get_secret_value())
        if not base:
            raise ModelError("Укажите Base URL сервиса через мастер настройки подключения.")
        if not s.model.strip():
            raise ModelError("Укажите ID модели в настройках подключения.")
        if self.preset["key"] and base == self.preset["url"] and not key and not extra_headers:
            raise ModelError("Для выбранного сервиса нужен API-ключ. Запустите мастер настройки подключения.")
        schema = response_schema(response_type)
        system = (
            "Ты — аналитический агент. Выполни инструкции и выбери следующий шаг. "
            "Верни только один JSON-объект по указанной JSON Schema, без Markdown и пояснений. "
            "Не исполняй команды и не вызывай внешние инструменты. Доступные действия описаны в запросе.\n"
            + json.dumps(schema, ensure_ascii=False)
        )
        mode = self.preset["format"] if s.model_api_format == "auto" else s.model_api_format
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = "Bearer " + key
        protocol = self.preset["protocol"]
        if protocol == "responses":
            endpoint = "/responses"
            body = {
                "model": s.model,
                "instructions": system,
                "input": prompt,
                "max_output_tokens": s.model_max_output_tokens,
                "store": False,
            }
            if mode == "json_schema":
                body["text"] = {
                    "format": {
                        "type": "json_schema",
                        "name": "agent_response",
                        "strict": True,
                        "schema": schema,
                    }
                }
            elif mode == "json_object":
                body["text"] = {"format": {"type": "json_object"}}
            if s.model_api_reasoning:
                body["reasoning"] = {"effort": s.model_api_reasoning}
        elif protocol == "anthropic":
            endpoint = "/messages"
            headers["anthropic-version"] = "2026-10-21"
            body = {
                "model": s.model,
                "system": system,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": s.model_max_output_tokens,
            }
            if mode != "prompt":
                body["tools"] = [
                    {
                        "name": "agent_response",
                        "description": "Следующий шаг аналитического агента",
                        "input_schema": schema,
                    }
                ]
                body["tool_choice"] = {"type": "tool", "name": "agent_response"}
        else:
            endpoint = "/chat/completions"
            body = {
                "model": s.model,
                "stream": False,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            }
            if mode == "json_schema":
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "agent_response", "strict": True, "schema": schema},
                }
            elif mode == "json_object":
                body["response_format"] = {"type": "json_object"}
        # Case-insensitive merge also allows installations using api-key/x-api-key authentication.
        headers = httpx.Headers(headers)
        headers.update(extra_headers)
        started = time.perf_counter()
        try:
            with httpx.Client(
                timeout=s.model_timeout, transport=self.transport, follow_redirects=False
            ) as client:
                response = client.post(base.rstrip("/") + endpoint, headers=headers, json=body)
        except httpx.TimeoutException:
            raise ModelError(f"Модель не ответила за {s.model_timeout} с. Состояние сохранено.") from None
        except (httpx.HTTPError, httpx.InvalidURL):
            raise ModelError(
                "Не удалось подключиться к API модели. Проверьте адрес сервиса, сеть и сертификат."
            ) from None
        if response.status_code >= 300:
            help_text = {
                400: "Проверьте ID модели, параметры и формат ответа (JSON Schema / JSON / по инструкции).",
                401: "Проверьте API-ключ.",
                403: "Проверьте права ключа и доступ к модели.",
                404: "Проверьте Base URL и ID модели.",
                429: "Исчерпан лимит запросов или баланс сервиса.",
            }.get(response.status_code, "Проверьте доступность сервиса и его настройки.")
            # Provider error bodies can echo secrets and input. Never surface or persist them.
            raise ModelError(f"API модели вернул HTTP {response.status_code}. {help_text}")
        try:
            payload = response.json()
            if protocol == "responses":
                if payload.get("status") not in {None, "completed"} or payload.get("error"):
                    raise ValueError
                if any(
                    item.get("type") not in {"message", "reasoning"} for item in payload.get("output", [])
                ):
                    raise ValueError
                content = [
                    c
                    for item in payload.get("output", [])
                    if item.get("type") == "message"
                    for c in item.get("content", [])
                ]
                if any(c.get("type") == "refusal" for c in content):
                    raise ValueError
                result = "".join(c.get("text", "") for c in content if c.get("type") == "output_text")
            elif protocol == "anthropic":
                if payload.get("stop_reason") not in {"tool_use", "end_turn", "stop_sequence"}:
                    raise ValueError
                blocks = payload.get("content", [])
                if mode != "prompt":
                    uses = [c for c in blocks if c.get("type") == "tool_use"]
                    if len(uses) != 1 or uses[0].get("name") != "agent_response":
                        raise ValueError
                    result = json.dumps(uses[0]["input"])
                else:
                    result = "".join(c.get("text", "") for c in blocks if c.get("type") == "text")
            else:
                choice = payload["choices"][0]
                if choice.get("finish_reason") not in {None, "stop"}:
                    raise ValueError
                message = choice["message"]
                if message.get("refusal") or message.get("tool_calls"):
                    raise ValueError
                result = message["content"]
                if isinstance(result, list):
                    result = "".join(c.get("text", "") for c in result if c.get("type") == "text")
            if not isinstance(result, str) or len(result) > 2_000_000:
                raise ValueError
            # Some prompt-only local models fence their JSON. Validation remains mandatory.
            result = result.strip()
            if result.startswith("```json\n") and result.endswith("```"):
                result = result[8:-3].strip()
            plan = response_type.model_validate_json(result)
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise ModelError(
                "Модель не вернула корректный JSON-план. Проверьте поддержку выбранного формата ответа; состояние сохранено."
            ) from None
        return plan, {
            "adapter": s.model_adapter,
            "provider": self.preset["label"],
            "model": s.model,
            "reasoning": s.model_api_reasoning or None,
            "model_ms": round((time.perf_counter() - started) * 1000, 1),
        }
