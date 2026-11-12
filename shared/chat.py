"""MY conversations with interactive dashboard snapshots, files and callbacks."""

import copy
import html
from datetime import datetime, timezone
from shared.agent import Agent
from shared.analytics import (
    present,
    materialize,
    comparison_frame,
    chart_frame,
    dashboard_kpis,
    live_chart_title,
    COL_LABELS,
)
from shared.charts import render
from shared.charts.registry import fmt, LABELS, RENDER_WIDTH, RENDER_DASHBOARD, RENDER_VERSION
from shared.exports import csv_export, html_export, xlsx_export, png_export
from shared.formatting import markdown_html
from shared.models import ChartSpec, uid
from shared.tools import Tools, Save
from shared.source import records
from shared.storage import pack, unpack
from shared.view_modes import METRIC_LABELS, view_notice


def stamp():
    return datetime.now(timezone.utc).isoformat()


class ChatService:
    def __init__(self, settings, store):
        self.settings = settings
        self.store = store
        self.agent = Agent(settings, store)
        from shared.view_cache import ViewCache

        self.views = ViewCache(store)

    def process(self, owner, state, text, request_id=None, progress=None, defer_archive=False):
        if request_id and any(
            m.get("request_id") == request_id and m["role"] == "assistant" for m in state["messages"]
        ):
            return next(
                m
                for m in reversed(state["messages"])
                if m.get("request_id") == request_id and m["role"] == "assistant"
            )
        working = (
            copy.deepcopy(
                {
                    key: state.get(key)
                    for key in [
                        "dataset",
                        "snapshot",
                        "filters",
                        "dashboard",
                        "calculations",
                        "calculation_history",
                        "saved_id",
                        "saved_dashboard_version",
                        "workspace",
                    ]
                }
            )
            if state.get("workspace")
            else None
        )
        result = self.agent.run(owner, state, text, progress=progress)
        from shared.dashboard_brief import archive_query

        query = archive_query(state, text)
        message = state["messages"][-1]
        message.update(
            id=uid(), created=stamp(), html=markdown_html(message["content"]), attachments=[], buttons=[]
        )
        presentation = result["presentation"]
        message["analysis_filters"] = copy.deepcopy(state["filters"])
        requested = presentation["charts"]
        if requested:
            indexes = (
                None
                if "*" in requested
                else [i for i, c in enumerate(state["dashboard"]["charts"]) if c["id"] in requested]
            )
            self.dashboard(owner, state, message, indexes, progress=progress)
        show_table = presentation["table"] or (
            "compute_column" in result["tools"]
            and message.get("intent") in {"calculation", "modify", "table"}
        )
        if show_table and state.get("dataset"):
            # A table is a working result too: retain its source for later edits.
            if not message.get("dashboard"):
                self.dashboard(owner, state, message)
            self.inline_table(owner, state, message)
            message["workspace_view"] = "table"
        formats = list(dict.fromkeys(presentation["exports"] + (["csv"] if presentation["table"] else [])))
        for format_ in formats:
            self.document(owner, state, message, format_)
        message["buttons"] = self.general_buttons(state, presentation["controls"])
        if message.get("dashboard"):
            message["buttons"] = self.general_buttons(
                state, [c for c in presentation["controls"] if c in {"csv", "save", "saved"}]
            )
        if "search_dashboards" in result["tools"] and "saved" in presentation["controls"]:
            self.saved_buttons(owner, state, message)
        self.rich_message(message)
        self._tag(state, request_id)
        if message.get("dashboard"):
            if defer_archive:
                state["_pending_archive"] = {
                    "message_id": message["id"],
                    "query": query,
                    "tools": result["tools"],
                }
            else:
                self.archive_dashboard(owner, state, message, query, result["tools"], progress)
        elif (
            working
            and "fetch_data" in result["tools"]
            and not {"open_dashboard", "save_dashboard"}.intersection(result["tools"])
        ):
            # A factual answer/empty fetch is not a request to discard the selected dashboard.
            state.update(working)
        return message

    def archive_dashboard(self, owner, state, message, query, tools, progress=None):
        last_saved = max(
            (i for i, name in enumerate(tools) if name in {"save_dashboard", "open_dashboard"}), default=-1
        )
        edits = {
            "fetch_data",
            "apply_filters",
            "compute_column",
            "create_dashboard",
            "create_chart",
            "update_chart",
        }
        already_saved = last_saved >= 0 and not any(name in edits for name in tools[last_saved + 1 :])
        continuing = state.get("workspace")
        if continuing:
            continuing["message_id"] = message["id"]
            if last_saved < 0:
                entry = next((d for d in self.store.list(owner) if d["id"] == continuing["saved_id"]), None)
                if entry:
                    state["saved_id"] = entry["id"]
                    state["dashboard"]["title"] = entry["title"]
        if not already_saved or not state.get("saved_id"):
            if progress:
                progress(phase="saving", label="Сохраняю дашборд", completed=None, total=None)
            Tools(self.settings, self.store, owner, state).save_dashboard(
                Save(title=state["dashboard"]["title"], as_new=not bool(continuing)),
                query=None if continuing else query,
            )
        message["dashboard"].update(saved_id=state["saved_id"], saved=True)

    def open_saved(self, owner, current, ident):
        from shared.dashboard_context import select_workspace

        state = self.store.open(owner, ident)
        state["messages"] = copy.deepcopy(current["messages"])
        state["chat_actions"] = copy.deepcopy(current.get("chat_actions", {}))
        state["revision"] = current["revision"]
        message = self._reply(state, f"Открыт дашборд «{state['dashboard']['title']}».", "storage")
        entry = next(d for d in self.store.list(owner) if d["id"] == ident)
        select_workspace(state, message["id"], entry)
        if self.settings.dashboard_brief_enabled:
            from shared.dashboard_brief import adopt_workspace

            adopt_workspace(state, current)
        if state["dashboard"]["charts"]:
            self.dashboard(owner, state, message)
        else:
            self.inline_table(owner, state, message)
        self.rich_message(message)
        return state

    def _tag(self, state, request_id):
        for m in state["messages"][-2:]:
            if request_id:
                m["request_id"] = request_id
            m.setdefault("id", uid())
            m.setdefault("created", stamp())
        state["messages"] = state["messages"][-self.settings.chat_history :]

    def _append_user(self, state, text):
        state["messages"].append({"id": uid(), "role": "user", "content": text, "created": stamp()})

    def _reply(self, state, text, intent="modify"):
        message = {
            "id": uid(),
            "role": "assistant",
            "content": text,
            "html": markdown_html(text),
            "intent": intent,
            "created": stamp(),
            "attachments": [],
            "buttons": [],
        }
        state["messages"].append(message)
        state["messages"] = state["messages"][-self.settings.chat_history :]
        return message

    def button(self, state, label, operation, arguments=None, guard=True):
        ident = uid()
        actions = state.setdefault("chat_actions", {})
        actions[ident] = {"operation": operation, "arguments": arguments or {}}
        # Bound metadata; old buttons then fail explicitly rather than operating on unrelated data.
        while len(actions) > 4000:
            actions.pop(next(iter(actions)))
        return {"text": label, "action_id": ident}

    def general_buttons(self, state, controls=None):
        controls = (
            controls
            if controls is not None
            else ["regions", "dimension", "granularity", "csv", "save", "saved"]
        )
        definitions = {
            "regions": ("Регион", "menu", {"kind": "regions"}),
            "dimension": ("Разрез", "menu", {"kind": "dimension"}),
            "granularity": ("Период", "menu", {"kind": "granularity"}),
            "csv": ("Таблица CSV", "export", {"format": "csv"}),
            "save": ("Сохранить", "save", {}),
            "saved": ("Мои отчёты", "saved", {}),
        }
        buttons = [self.button(state, *definitions[name]) for name in controls if name in definitions]
        return [buttons[i : i + 3] for i in range(0, len(buttons), 3)]

    def dashboard(self, owner, state, message, indexes=None, progress=None):
        from shared.dashboard_context import filter_options

        message.setdefault("id", uid())

        # Only exact source/formula/filter/config/viewport matches may be reused.
        message.setdefault(
            "analysis_filters", copy.deepcopy(message.get("dashboard", {}).get("filters", state["filters"]))
        )
        cache_key, source_signature = self.views.key(state, message, indexes)
        cached = self.views.get(owner, message["id"], cache_key)
        if cached:
            cached.update(
                saved_id=state.get("saved_id"),
                saved_title=state["dashboard"]["title"],
                saved=bool(state.get("saved_id"))
                and state.get("saved_dashboard_version") == state["dashboard"]["version"],
                settings_revision=message.get("dashboard", {}).get("settings_revision", 0),
                cache_hit=True,
            )
            message["dashboard"] = cached
            return

        df, _, _ = materialize(state)
        comparison = comparison_frame(state)
        configs = state["dashboard"]["charts"][:4]
        charts = []
        selected = [(i, c) for i, c in enumerate(configs) if indexes is None or i in indexes]
        width, height = RENDER_WIDTH.get() or 600, 290
        viewport = RENDER_DASHBOARD.get()
        if viewport and selected:
            columns = (
                2
                if viewport[0] >= 760 and len(selected) > 1 and state["dashboard"]["layout"] != "one-column"
                else 1
            )
            rows = (len(selected) + columns - 1) // columns
            width = min(1200, max(320, (viewport[0] - 16 * (columns - 1)) // columns))
            extra = 36 if view_notice(state["filters"]) else 0
            extra += 20 if len(selected) == 1 else 100 if viewport[0] >= 1040 else 210
            decoration = 84 if state["filters"]["value_mode"] == "dynamics" else 60
            height = min(
                360, max(100 if extra else 120, (viewport[1] - 190 - extra - rows * decoration) // rows)
            )
            if viewport[0] < 760:
                # A phone uses normal vertical flow; keep each chart readable.
                height = min(320, max(240, viewport[1] - 360))
        message.setdefault(
            "analysis_filters", copy.deepcopy(message.get("dashboard", {}).get("filters", state["filters"]))
        )
        changed = message["analysis_filters"] != state["filters"]
        for count, (index, conf) in enumerate(selected):
            if progress:
                progress(phase="render", label="Собираю дашборд", completed=count, total=len(selected))
            spec = ChartSpec.model_validate(conf).model_copy(update={"width": width, "height": height})
            frame, effective = chart_frame(df, spec, state["filters"], state["calculations"], comparison)
            if changed:
                effective.title = live_chart_title(effective, state["filters"], state["calculations"])
            chart = render(frame, effective)
            chart.update(spec=effective.model_dump(), index=index + 1)
            charts.append(chart)
        saved = (
            bool(state.get("saved_id"))
            and state.get("saved_dashboard_version") == state["dashboard"]["version"]
        )
        title = state["dashboard"]["title"]
        title_filters = message.get("dashboard", {}).get("title_filters", message["analysis_filters"])
        if title_filters != state["filters"]:
            f = state["filters"]
            title = "Продажи" if f["metric"] == "amount" else METRIC_LABELS[f["metric"]]
            if f.get("start") and f.get("end") and f["start"][:4] == f["end"][:4]:
                title += " · " + f["start"][:4]
        observed_end = state["dataset"].get("observed_end")
        coverage = ""
        if observed_end and state["filters"].get("end") and state["filters"]["end"] > observed_end:
            coverage = f"Продажи в источнике доступны по {observed_end}. За последующие месяцы фактических данных нет."
        message["dashboard"] = {
            "id": state["dashboard"]["id"],
            "settings_revision": message.get("dashboard", {}).get("settings_revision", 0),
            "title": title,
            "title_filters": copy.deepcopy(title_filters),
            "filters": copy.deepcopy(state["filters"]),
            "filter_notice": state.get("filter_notice", ""),
            "coverage_notice": coverage,
            "view_notice": view_notice(state["filters"]),
            "saved_id": state.get("saved_id"),
            "saved_title": state["dashboard"]["title"],
            "saved": saved,
            "kpis": dashboard_kpis(state, df),
            "period": [state["dataset"]["query_start"], state["dataset"]["query_end"]],
            "charts": charts,
            "layout": state["dashboard"]["layout"],
            "source_specs": copy.deepcopy(configs),
            "options": filter_options(state),
            "editable": True,
            "render_profile": list(viewport) if viewport else None,
            "render_version": RENDER_VERSION,
            "sync_zoom": state["dashboard"].get("sync_zoom", False),
            "table_id": state["dataset"]["id"],
            "table_version": state["dataset"]["version"],
            "cache_hit": False,
            "cache_engine": self.views.engine,
            "source_signature": source_signature,
            "calculation_names": [c["name"] for c in state["calculations"]],
        }
        self.views.put(owner, message["id"], cache_key, message["dashboard"])
        # Historical image attachments are represented by the single dashboard now.
        message["attachments"] = [a for a in message.get("attachments", []) if a["kind"] != "photo"]
        if progress:
            progress(phase="render", label="Дашборд собран", completed=len(selected), total=len(selected))

    def refresh_dashboard(self, owner, state):
        if not state["dataset"] or not state["dashboard"]["charts"]:
            return
        for chart in state["dashboard"]["charts"]:
            if all(y in chart.get("hidden", []) for y in chart["y"]):
                chart["hidden"] = []
        from shared.dashboard_context import active_message

        message = active_message(state) or next(
            (
                m
                for m in reversed(state["messages"])
                if (
                    m.get("dashboard", {}).get("table_id") == state["dataset"]["id"]
                    and m["dashboard"]["id"] == state["dashboard"]["id"]
                )
                or (
                    not m.get("dashboard") and any(a.get("kind") == "photo" for a in m.get("attachments", []))
                )
            ),
            None,
        )
        if message:
            self.dashboard(owner, state, message)

    def rich_message(self, message):
        # Only the model's text is formatted; dashboard DOM is a separate typed object.
        message["markup"] = message.get("formatted_html") or markdown_html(message["content"])
        message.pop("rich_html", None)

    def inline_table(self, owner, state, message):
        df, _, _ = materialize(state)
        labels = {**COL_LABELS, **{c["name"]: c["label"] for c in state["calculations"]}}
        columns = [
            {"name": name, "label": labels.get(name, name), "type": str(df[name].dtype)}
            for name in df.columns
            if name not in {"outlet_id", "product_id", "client_id"}
        ]
        snapshot = {
            "title": "Таблица данных",
            "columns": columns,
            "rows": records(df[[c["name"] for c in columns]]),
            "filters": copy.deepcopy(state["filters"]),
            "created": message["created"],
        }
        # Keep an immutable snapshot outside conversation JSON; only a page goes to the UI.
        media = self.store.add_media(
            owner, pack(snapshot), "puzzle-table.json.gz", "application/vnd.puzzle.table+gzip"
        )
        message["table"] = self.table_page(owner, media["id"])

    def table_page(self, owner, ident, offset=0, limit=25):
        media = self.store.media(owner, ident)
        if media["mime"] != "application/vnd.puzzle.table+gzip":
            raise ValueError("Таблица не найдена или недоступна")
        snapshot = unpack(media["body"])
        rows = snapshot.pop("rows")
        total = len(rows)
        offset = min(offset, max(0, (total - 1) // limit * limit))
        return {
            **snapshot,
            "id": ident,
            "rows": rows[offset : offset + limit],
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    def document(self, owner, state, message, format_, index=1):
        if format_ == "csv":
            body = csv_export(state).encode("utf-8")
            mime = "text/csv"
            filename = "puzzle-data.csv"
        elif format_ == "html":
            body = html_export(state).encode("utf-8")
            mime = "text/html"
            filename = "puzzle-dashboard.html"
        elif format_ in {"xlsx", "png"}:
            body = xlsx_export(state) if format_ == "xlsx" else png_export(state)
            mime = (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                if format_ == "xlsx"
                else "image/png"
            )
            filename = f"puzzle-{'data' if format_ == 'xlsx' else 'dashboard'}.{format_}"
        elif format_ == "svg":
            view = present(state)
            if index > len(view["charts"]):
                raise ValueError("График не найден")
            body = view["charts"][index - 1]["svg"].encode()
            mime = "image/svg+xml"
            filename = f"puzzle-chart-{index}.svg"
        else:
            raise ValueError("Неизвестный формат экспорта")
        media = self.store.add_media(owner, body, filename, mime)
        message["attachments"].append({**media, "kind": "document", "title": filename, "buttons": []})

    def saved_buttons(self, owner, state, message):
        items = self.store.list(owner)
        message["buttons"] = [
            [self.button(state, item["title"][:55], "open", {"id": item["id"]}, guard=False)]
            for item in items[:15]
        ]

    def saved(self, owner, state):
        items = self.store.list(owner)
        text = "Ваши сохранённые отчёты:\n" + (
            "\n".join("• " + i["title"] for i in items[:15]) if items else "Пока нет сохранённых отчётов."
        )
        message = self._reply(state, text, "storage")
        self.saved_buttons(owner, state, message)
        return message

    def action(self, owner, state, ident, replace_id=None):
        old = next((m for m in state["messages"] if m.get("id") == replace_id), None)
        result = self._action(owner, state, ident)
        if old and state["chat_actions"][ident]["operation"] == "menu":
            result["dashboard"] = old.get("dashboard")
            result["buttons"].append([self.button(state, "Назад к графикам", "refresh")])
        self.rich_message(result)
        if replace_id:
            current = next(
                (i for i, m in enumerate(state["messages"]) if m.get("id") == replace_id and m is not result),
                None,
            )
            if current is not None:
                state["messages"].remove(result)
                result["id"] = replace_id
                state["messages"][current] = result
        return result

    def _action(self, owner, state, ident):
        action = state.get("chat_actions", {}).get(ident)
        if not action:
            raise ValueError("Кнопка устарела. Используйте кнопки последнего ответа или напишите запрос.")
        op, args = action["operation"], action["arguments"]
        tools = Tools(self.settings, self.store, owner, state)
        if op == "menu":
            return self.menu(state, args["kind"])
        if op == "saved":
            return self.saved(owner, state)
        if op == "values":
            index = args["index"]
            df, _, _ = materialize(state)
            spec = ChartSpec.model_validate(state["dashboard"]["charts"][index])
            frame, effective = chart_frame(df, spec, state["filters"], state["calculations"])
            lines = [effective.title]
            for _, row in frame.head(20).iterrows():
                lines.append(
                    str(row[effective.x])
                    + ": "
                    + ", ".join(
                        LABELS.get(y, y) + " = " + fmt(row[y], effective.number_format) for y in effective.y
                    )
                )
            if len(frame) > 20:
                lines.append(f"Показаны 20 из {len(frame)} групп; полная таблица доступна CSV.")
            message = self._reply(state, "Точные значения", "table")
            message["html"] = "<pre>" + html.escape("\n".join(lines)) + "</pre>"
            message["formatted_html"] = message["html"]
            message["buttons"] = [[self.button(state, "Скачать CSV", "export", {"format": "csv"})]]
            return message
        if op == "export":
            message = self._reply(
                state,
                "Таблица с текущими фильтрами и вычислениями."
                if args["format"] == "csv"
                else "Визуализация во вложении.",
                "storage",
            )
            self.document(owner, state, message, args["format"])
            return message
        if op == "save":
            result = tools.invoke(
                "save_dashboard", {"title": state["dashboard"]["title"], "as_new": bool(args.get("as_new"))}
            )
            message = self._reply(state, f"Отчёт «{result['title']}» сохранён.", "storage")
            message["buttons"] = [
                [
                    self.button(state, "Сохранить копию", "save", {"as_new": True}),
                    self.button(state, "Мои отчёты", "saved", guard=False),
                ]
            ]
            return message
        indexes = None
        if op == "filters":
            tools.invoke("apply_filters", {"filters": args})
        elif op == "refresh":
            pass
        elif op == "open":
            # Preserve actual conversation; a saved table/config doesn't replace the chat history.
            conversation = copy.deepcopy(state["messages"])
            tools.invoke("open_dashboard", args)
            state["messages"] = conversation
        elif op in {"series", "zoom", "reset_zoom"}:
            index = args["index"]
            chart = state["dashboard"]["charts"][index]
            if op == "series":
                hidden = list(chart["hidden"])
                series = args["series"]
                if series in hidden:
                    hidden.remove(series)
                else:
                    hidden.append(series)
                changes = {"hidden": hidden}
            elif op == "reset_zoom":
                changes = {"zoom": None}
            else:
                axes = args["axes"]
                x0, x1 = axes["xlim"]
                y0, y1 = axes["ylim"]
                changes = {
                    "zoom": [
                        min(x0, x1) + (abs(x1 - x0) * 0.2),
                        max(x0, x1) - (abs(x1 - x0) * 0.2),
                        min(y0, y1),
                        max(y0, y1),
                    ]
                }
            tools.invoke("update_chart", {"index": index + 1, "changes": changes})
        else:
            raise ValueError("Неизвестное действие")
        message = self._reply(state, "")
        self.dashboard(owner, state, message, indexes)
        message["buttons"] = self.general_buttons(state)
        message["buttons"].append(
            [
                self.button(
                    state,
                    "Сбросить фильтры",
                    "filters",
                    {"start": None, "end": None, "regions": [], "outlets": [], "products": [], "clients": []},
                )
            ]
        )
        return message

    def menu(self, state, kind):
        if kind == "regions":
            df, _, _ = materialize(state)
            # Options come from the loaded table, not the already-filtered region subset.
            regions = sorted({row["region"] for row in state["dataset"]["rows"]})
            message = self._reply(state, "Выберите регион. Графики и расчёты обновятся прямо в чате.")
            message["buttons"] = [
                [self.button(state, "Все регионы", "filters", {"regions": [], "outlets": []})]
            ] + [[self.button(state, v, "filters", {"regions": [v], "outlets": []})] for v in regions]
        elif kind == "dimension":
            message = self._reply(state, "Выберите разрез.")
            message["buttons"] = [
                [
                    self.button(state, label, "filters", {"dimension": v})
                    for v, label in [("region", "Регионы"), ("outlet", "Точки"), ("product", "Продукты")]
                ]
            ]
        elif kind == "granularity":
            message = self._reply(state, "Выберите детализацию времени.")
            message["buttons"] = [
                [
                    self.button(state, label, "filters", {"granularity": v})
                    for v, label in [("month", "Месяц"), ("quarter", "Квартал"), ("year", "Год")]
                ]
            ]
        else:
            raise ValueError("Неизвестное меню")
        return message
