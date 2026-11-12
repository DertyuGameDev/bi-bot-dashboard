"""Replaceable planner adapter. Codex never receives DB/TG credentials or entire tables."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from shared.models import AgentPlan, AgentStep


class ModelError(ValueError):
    pass


def response_schema(response_type):
    schema = response_type.model_json_schema()

    def strict(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                # Structured outputs require nullable optional fields to be present too.
                node["required"] = list(node["properties"])
            for child in node.values():
                strict(child)
        elif isinstance(node, list):
            for child in node:
                strict(child)

    strict(schema)
    return schema


class CodexAdapter:
    def __init__(self, settings):
        self.settings = settings

    def step(self, prompt):
        return self._generate(prompt, AgentStep)

    def plan(self, prompt):
        return self._generate(prompt, AgentPlan)

    def classify(self, prompt):
        from shared.preflight import Routing

        return self._generate(prompt, Routing)

    def _generate(self, prompt, response_type):
        s = self.settings
        # Preserve authentication via CODEX_HOME; do not inherit application secrets.
        env = {
            k: v
            for k, v in os.environ.items()
            if k
            in {
                "PATH",
                "HOME",
                "USER",
                "LOGNAME",
                "TMPDIR",
                "CODEX_HOME",
                "SSL_CERT_FILE",
                "SSL_CERT_DIR",
                "HTTPS_PROXY",
                "HTTP_PROXY",
                "ALL_PROXY",
                "NO_PROXY",
            }
        }
        start = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="puzzle-planner-") as work:
            schema = Path(work) / "response-schema.json"
            schema.write_text(json.dumps(response_schema(response_type)))
            output = Path(work) / "response.json"
            command = [
                "codex",
                "exec",
                "--ignore-user-config",
                "--ignore-rules",
                "--ephemeral",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "--cd",
                work,
                "--model",
                s.model,
                "-c",
                f'model_reasoning_effort="{s.reasoning}"',
                "-c",
                "features.shell_tool=false",
                "-c",
                "features.unified_exec=false",
                "-c",
                "features.multi_agent=false",
                "-c",
                "features.skill_search=false",
                "-c",
                "features.skip_host_skill_discovery=true",
                "-c",
                'web_search="disabled"',
                "--output-schema",
                str(schema),
                "--output-last-message",
                str(output),
                "--json",
                "-",
            ]
            try:
                result = subprocess.run(
                    command,
                    input=prompt,
                    text=True,
                    capture_output=True,
                    timeout=s.model_timeout,
                    env=env,
                    cwd=work,
                    start_new_session=True,
                )
            except subprocess.TimeoutExpired as e:
                # subprocess.run kills/waits the child on timeout; no silent adapter/model fallback.
                raise ModelError(
                    f"Модель {s.model} не ответила за {s.model_timeout} с. Повторите запрос; состояние сохранено."
                ) from e
            if result.returncode or not output.exists():
                raise ModelError(
                    f"Codex не вернул план для {s.model}. Проверьте codex login status и доступность модели. Автоматическая подмена отключена."
                )
            # Refuse unexpected built-in tool usage even if a CLI feature changes upstream.
            for line in result.stdout.splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                item = event.get("item", {})
                if item.get("type") in {"command_execution", "mcp_tool_call", "web_search", "file_change"}:
                    raise ModelError("Codex попытался использовать встроенный инструмент. План не исполнен.")
            try:
                plan = response_type.model_validate_json(output.read_text())
            except (ValueError, OSError) as e:
                raise ModelError(
                    "Модель вернула невалидный структурированный план. Состояние сохранено."
                ) from e
        return plan, {
            "adapter": "codex",
            "model": s.model,
            "reasoning": s.reasoning,
            "model_ms": round((time.perf_counter() - start) * 1000, 1),
        }


class DemoAdapter:
    """Explicitly selected deterministic scenario runner; never presented as AI."""

    def __init__(self, settings):
        self.settings = settings

    def step(self, prompt):
        data = json.loads(prompt)
        observations = data.get("observations", [])
        if data["request"].strip().lower() in {"/start", "/help", "привет", "здравствуйте"}:
            return AgentStep(
                kind="final",
                decision="Демо-приветствие",
                calls=[],
                answer="Здравствуйте! Задайте вопрос о данных.",
                intent="text",
                charts=[],
                table=False,
                exports=[],
                controls=[],
            ), self._metadata()
        plan, meta = self.plan(prompt)
        completed = {o["tool"] for o in observations}
        if not observations or observations[-1].get("status") != "error":
            next_call = next((call for call in plan.calls if call.tool not in completed), None)
            if next_call:
                return AgentStep(
                    kind="act",
                    decision="Следующая демо-операция",
                    calls=[next_call],
                    answer="",
                    intent=plan.intent,
                    charts=[],
                    table=False,
                    exports=[],
                    controls=[],
                ), meta
        observations = data["observations"]
        tool_names = [o["tool"] for o in observations]
        if observations[-1].get("status") == "error":
            return AgentStep(
                kind="final",
                decision="Ошибка демо-сценария",
                calls=[],
                answer="Демо-сценарий: " + observations[-1]["error"],
                intent="text",
                charts=[],
                table=False,
                exports=[],
                controls=[],
            ), self._metadata()
        intent = plan.intent
        profile = next((o["result"] for o in reversed(observations) if o.get("tool") == "profile_table"), {})
        answer = profile.get("summary", "Готово.")
        if "save_dashboard" in tool_names:
            saved = next(o["result"] for o in observations if o["tool"] == "save_dashboard")
            answer = f"Отчёт «{saved['title']}» сохранён."
        visual = any(
            n in tool_names
            for n in {"create_dashboard", "create_chart", "update_chart", "apply_filters", "open_dashboard"}
        ) or ("compute_column" in tool_names and bool(data["context"]["dashboard"]["charts"]))
        table = intent == "table"
        return AgentStep(
            kind="final",
            decision="Демо-результат",
            calls=[],
            answer=answer,
            intent=intent,
            charts=["*"] if visual else [],
            table=table,
            exports=["csv"] if table else [],
            controls=["regions", "dimension", "granularity", "csv", "save", "saved"]
            if visual or table
            else [],
        ), self._metadata()

    def _metadata(self):
        return {
            "adapter": "demo",
            "model": "Детерминированный сценарий, без ИИ",
            "reasoning": None,
            "model_ms": 0,
        }

    def plan(self, prompt):
        data = json.loads(prompt)
        text = data["request"].lower()
        calls = []
        intent = "text"

        def add(tool, args=None):
            calls.append({"tool": tool, "arguments": json.dumps(args or {}, ensure_ascii=False)})

        if "сохран" in text or "копи" in text:
            import re

            names = re.findall(r'[«"“](.*?)[»"”]', data["request"])
            add(
                "save_dashboard",
                {"title": names[0] if names else "Продажи Q3", "as_new": "копи" in text or "новый" in text},
            )
            intent = "storage"
        elif "мои дашборд" in text or "найди дашборд" in text:
            add("search_dashboards")
            intent = "storage"
        elif "отмени" in text:
            add("compute_column", {"action": "undo"})
            intent = "calculation"
        elif "трет" in text:
            add(
                "update_chart",
                {"index": 3, "changes": {"sort": "completion", "title": "Рейтинг по выполнению плана"}},
            )
            intent = "modify"
        elif any(v in text for v in ["колонк", "посчитай", "вычислен", "формул"]):
            expression = "fact / plan * 100"
            name = "completion"
            label = "Выполнение плана, %"
            kind = "formula"
            if "отклонен" in text:
                expression = "fact - plan"
                name = "deviation"
                label = "Отклонение факта от плана"
            for token, k in [
                ("прошлому году", "yoy"),
                ("предыдущему месяцу", "mom"),
                ("прошлому месяцу", "mom"),
                ("долю", "share"),
                ("накопитель", "cumulative"),
                ("медиан", "median"),
                ("средн", "mean"),
                ("ранг", "rank"),
            ]:
                if token in text:
                    kind = k
                    name = k
                    label = k
            add(
                "compute_column",
                {
                    "action": "add",
                    "calculation": {
                        "name": name,
                        "label": label,
                        "kind": kind,
                        "expression": expression,
                        "column": "fact",
                        "group_by": ["region"] if kind != "formula" else [],
                    },
                },
            )
            intent = "calculation"
        elif "северо" in text or "центральн" in text:
            add(
                "apply_filters",
                {"filters": {"regions": ["Северо-Западный" if "северо" in text else "Центральный"]}},
            )
            intent = "modify"
        elif "таблиц" in text:
            if not data["context"].get("table"):
                add("fetch_data")
            add("profile_table")
            intent = "table"
        elif "дашборд" in text or "график" in text:
            if not data["context"].get("table"):
                add("fetch_data")
            add("create_dashboard", {"title": "Продажи · 2025", "charts": None})
            intent = "dashboard"
        elif not data["context"].get("table"):
            add("fetch_data")
            add("profile_table")
        else:
            add("profile_table")
        return AgentPlan(intent=intent, clarification="", calls=calls), {
            "adapter": "demo",
            "model": "Детерминированный сценарий, без ИИ",
            "reasoning": None,
            "model_ms": 0,
        }
