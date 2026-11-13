from collections import defaultdict
import re
import secrets
import threading
from typing import Literal
from fastapi import FastAPI, Request, Response, HTTPException, Depends, Query
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from shared.config import Settings, ROOT
from shared.model_providers import provider_info
from shared.storage import Store, Conflict
from shared.chat import ChatService
from MY.jobs import AnalysisJobs
from contextlib import asynccontextmanager
from shared.analytics import present, empty_state
from shared.dashboard_context import active_message, select_workspace
from shared.charts.registry import RENDER_WIDTH, RENDER_DASHBOARD, RENDER_VERSION
from shared.models import Strict
from shared.tools import Tools, Save
from shared.exports import csv_export, html_export, xlsx_export, png_export
from pydantic import Field, ValidationError


class ChatRequest(Strict):
    message: str = Field(min_length=1, max_length=4000)
    revision: int = Field(ge=0)
    request_id: str | None = Field(None, max_length=90)


class ActionRequest(Strict):
    revision: int = Field(ge=0)
    action_id: str = Field(min_length=1, max_length=64)
    message_id: str | None = Field(None, max_length=64)


class Mutation(Strict):
    revision: int = Field(ge=0)
    arguments: dict
    message_id: str | None = Field(None, max_length=64)


class OpenSaved(Strict):
    as_new: bool = False


class ConversationName(Strict):
    title: str = Field(min_length=1, max_length=80)


def create_app(settings=None):
    settings = settings or Settings()
    store = Store(settings.storage)
    chat_service = ChatService(settings, store)
    app_chat_service = chat_service

    @asynccontextmanager
    async def lifespan(app):
        from shared.library_migration import migrate_shared_library

        migrate_shared_library(store)
        jobs.interrupt_unfinished()
        yield
        jobs.close()

    app = FastAPI(title="BI-бот дашборд", lifespan=lifespan)
    app.state.chat_service = app_chat_service
    app.state.store = store
    app.state.settings = settings
    locks = defaultdict(threading.RLock)
    analysis_locks = defaultdict(threading.RLock)

    @app.middleware("http")
    async def headers(request, call_next):
        try:
            width = int(request.headers.get("X-Chart-Width", "640"))
        except ValueError:
            width = 640
        token = RENDER_WIDTH.set(max(320, min(1200, width)))
        try:
            size = (
                max(320, min(1600, int(request.headers["X-Dashboard-Width"]))),
                max(300, min(1400, int(request.headers["X-Dashboard-Height"]))),
            )
        except (ValueError, KeyError):
            size = None
        dashboard_token = RENDER_DASHBOARD.set(size)
        try:
            response = await call_next(request)
        finally:
            RENDER_WIDTH.reset(token)
            RENDER_DASHBOARD.reset(dashboard_token)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; font-src 'self'; frame-ancestors 'self'; base-uri 'none'; object-src 'none'"
        )
        # Mutation requests cannot be issued cross-origin with the required custom header.
        return response

    def account(request: Request):
        ident = request.cookies.get("puzzle_session", "")
        if not re.fullmatch("[a-f0-9]{64}", ident):
            raise HTTPException(401, "Откройте приложение заново для создания сессии")
        if request.method not in {"GET", "HEAD"} and request.headers.get("X-Requested-With") != "puzzle-rpa":
            raise HTTPException(403, "Отсутствует заголовок защиты запроса")
        return "web:" + ident

    def owner(request: Request):
        return store.conversation_scope(account(request), request.headers.get("X-Conversation-ID", "main"))

    @app.get("/api/conversations")
    def conversations(who=Depends(account)):
        return {"items": store.conversations(who)}

    @app.post("/api/conversations", status_code=201)
    def new_conversation(who=Depends(account)):
        return store.new_conversation(who)

    @app.patch("/api/conversations/{ident}")
    def rename_conversation(ident: str, body: ConversationName, who=Depends(account)):
        return store.rename_conversation(who, ident, body.title)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        if isinstance(exc, Conflict):
            return JSONResponse({"detail": str(exc)}, status_code=409)
        if isinstance(exc, ValidationError):
            return JSONResponse(
                {"detail": "Некорректные параметры. Проверьте колонки, формулу и фильтры."}, status_code=422
            )
        return JSONResponse({"detail": str(exc)[:500]}, status_code=400)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        content = (ROOT / "MY/index.html").read_text()
        ident = request.cookies.get("puzzle_session")
        if not ident or not re.fullmatch(r"[a-f0-9]{64}", ident):
            ident = secrets.token_hex(32)
        draft_key = store.draft("web:" + ident, "conversation")["storage_key"]
        content = content.replace("</head>", f'<meta name="draft-key" content="{draft_key}"></head>')
        response = HTMLResponse(content)
        if request.cookies.get("puzzle_session") != ident:
            response.set_cookie(
                "puzzle_session",
                ident,
                httponly=True,
                samesite="lax",
                secure=settings.secure_cookie,
                max_age=365 * 24 * 3600,
            )
        return response

    @app.get("/assets/{filename}")
    def assets(filename: str):
        from importlib.resources import files as package_files

        files = {
            "app.js": ROOT / "MY/app.js",
            "chat-html.js": ROOT / "MY/chat-html.js",
            "style.css": ROOT / "MY/style.css",
            "chart-widget.js": ROOT / "shared/chart-widget.js",
            "plotly.min.js": str(package_files("plotly").joinpath("package_data/plotly.min.js")),
            "plotly-locale-ru.js": ROOT / "shared/vendor/plotly-locale-ru.js",
            "dashboard.js": ROOT / "MY/dashboard.js",
            "chat-view.js": ROOT / "MY/chat-view.js",
            "outlet-picker.js": ROOT / "MY/outlet-picker.js",
            "filter-picker.js": ROOT / "MY/filter-picker.js",
            "chat-table.js": ROOT / "MY/chat-table.js",
            "workspace-table.js": ROOT / "MY/workspace-table.js",
            "dashboard-features.js": ROOT / "MY/dashboard-features.js",
            "conversations.js": ROOT / "MY/conversations.js",
            "dashboard-cache.js": ROOT / "MY/dashboard-cache.js",
        }
        if filename not in files:
            raise HTTPException(404)
        return FileResponse(
            files[filename], media_type="text/javascript" if filename.endswith(".js") else "text/css"
        )

    @app.get("/favicon.ico")
    def favicon():
        return Response(status_code=204)

    @app.get("/api/health")
    def health():
        return {
            "ok": True,
            "source": settings.source,
            "model_adapter": settings.model_adapter,
            "model": settings.model,
            **provider_info(settings),
            "reasoning": settings.reasoning
            if settings.model_adapter == "codex"
            else settings.model_api_reasoning or None,
            "independent_cards": True,
            "dashboard_brief": settings.dashboard_brief_enabled,
        }

    @app.get("/api/state")
    def state(who=Depends(owner)):
        with locks[who]:
            state = store.load(who)
            store.prepare_cards(who, state)
            chat_service.refresh_dashboard(who, state)
            fit_previous_cards(who, state)
            return present(state)

    @app.get("/api/table")
    def table(offset: int = Query(0, ge=0), limit: int = Query(60, ge=1, le=200), who=Depends(owner)):
        return present(store.load(who), False, offset, limit)

    @app.get("/api/chat/tables/{ident}")
    def chat_table(
        ident: str,
        offset: int = Query(0, ge=0),
        limit: int = Query(25, ge=1, le=200),
        who=Depends(owner),
    ):
        return chat_service.table_page(who, ident, offset, limit)

    def current(who, revision):
        state = store.load(who)
        if state["revision"] != revision:
            raise Conflict("Состояние изменилось в другом запросе. Повторите действие с последней версией.")
        return state

    def finish(who, state, revision, refresh=True, autosave=False):
        store.prepare_cards(who, state)
        if refresh:
            chat_service.refresh_dashboard(who, state)
        fit_previous_cards(who, state)
        # Render/validate before persistence; an invalid chart cannot replace a valid state.
        view = present(state)
        store.commit(who, state, revision, autosave=autosave)
        view["revision"] = state["revision"]
        view["saved_id"] = state["saved_id"]
        return view

    def fit_previous_cards(who, state):
        if not RENDER_DASHBOARD.get():
            return
        current_card = active_message(state)
        for message in state["messages"]:
            data = message.get("dashboard")
            if not data or data.get("editable") is False or message is current_card:
                continue
            if (
                data.get("render_profile") == list(RENDER_DASHBOARD.get())
                and data.get("render_version") == RENDER_VERSION
            ):
                continue
            scope = store.card(who, state, message["id"])
            chat_service.dashboard(who, scope, scope["messages"][-1])
            message["dashboard"] = scope["messages"][-1]["dashboard"]

    def edit_card(who, body, operation):
        session = current(who, body.revision)
        message = active_message(session)
        latest = (
            message
            and message.get("id") == body.message_id
            and session.get("dataset")
            and message["dashboard"]["table_id"] == session["dataset"]["id"]
            and message["dashboard"]["id"] == session["dashboard"]["id"]
        )
        if not body.message_id or latest:
            Tools(settings, store, who, session).invoke(
                operation, {"filters": body.arguments} if operation == "apply_filters" else body.arguments
            )
            if settings.dashboard_brief_enabled:
                from shared.dashboard_brief import sync_bound

                sync_bound(session)
            return finish(who, session, body.revision, autosave=operation != "save_dashboard")
        scope = store.card(who, session, body.message_id)
        save = {"title": scope["dashboard"]["title"], "update_caption": False}
        if operation == "save_dashboard":
            save = Save.model_validate(body.arguments).model_dump()
        else:
            Tools(settings, store, who, scope).invoke(
                operation, {"filters": body.arguments} if operation == "apply_filters" else body.arguments
            )
        chat_service.dashboard(who, scope, scope["messages"][-1])
        present(scope)  # Validate before either the library or conversation is written.
        store.commit(who, session, body.revision, card_update=(body.message_id, scope, save))
        store.prepare_cards(who, session)
        fit_previous_cards(who, session)
        return present(session)

    @app.post("/api/chat")
    def chat(body: ChatRequest, who=Depends(owner)):
        with locks[who]:
            state = store.load(who)
            if body.request_id and any(
                m.get("request_id") == body.request_id and m["role"] == "assistant" for m in state["messages"]
            ):
                return present(state)
            state = current(who, body.revision)
            store.prepare_cards(who, state)
            chat_service.process(who, state, body.message, body.request_id)
            return finish(
                who,
                state,
                body.revision,
                refresh=False,
                autosave=bool(state.get("workspace"))
                and state.get("saved_dashboard_version") != state["dashboard"]["version"],
            )

    def run_job(who, request, progress):
        body = ChatRequest.model_validate(request)
        with analysis_locks[who]:
            with locks[who]:
                state = store.load(who)
                if body.request_id and any(
                    m.get("request_id") == body.request_id and m["role"] == "assistant"
                    for m in state["messages"]
                ):
                    return present(state)
                base_ids = {m["id"] for m in state["messages"]}
                base_active = active_message(state)
                base_id = base_active["id"] if base_active else None
                base_card_revision = store.card_revision(who, base_id) if base_id else None
                base_filters = state["filters"].copy()
                from shared.dashboard_brief import KEY as BRIEF_KEY
                import copy

                base_brief = copy.deepcopy(state.get(BRIEF_KEY))
                store.prepare_cards(who, state)
            # Slow model/source work uses its own snapshot, not the conversation lock.
            chat_service.process(
                who, state, body.message, body.request_id, progress=progress, defer_archive=True
            )
            progress(phase="saving", label="Сохраняю результат", completed=None, total=None)
            with locks[who]:
                live = store.load(who)
                pending = state.pop("_pending_archive", None)
                changed = base_id and store.card_revision(who, base_id) != base_card_revision
                additions = [m for m in state["messages"] if m["id"] not in base_ids]
                state["messages"] = (live["messages"] + additions)[-settings.chat_history :]
                state["chat_actions"] = {**live.get("chat_actions", {}), **state.get("chat_actions", {})}
                if live.get(BRIEF_KEY) != base_brief:
                    # A new analysis or explicit workspace selection superseded this request's brief.
                    if BRIEF_KEY in live:
                        state[BRIEF_KEY] = copy.deepcopy(live[BRIEF_KEY])
                    else:
                        state.pop(BRIEF_KEY, None)
                    if settings.dashboard_brief_enabled:
                        from shared.dashboard_brief import context as brief_context

                        state["debug"][BRIEF_KEY] = brief_context(state)
                if changed and pending:
                    # Parallel edits keep the original card; the AI result becomes a copy.
                    original = store.card(who, live, base_id)
                    if original.get("saved_id") == state.get("saved_id"):
                        store.save(who, original, original["dashboard"]["title"])
                        state["saved_id"] = None
                        state["workspace"] = None
                elif changed and not pending and state["filters"] == base_filters:
                    original = store.card(who, live, base_id)
                    state["filters"] = original["filters"]
                    state["snapshot"] = None
                    chat_service.refresh_dashboard(who, state)
                if pending:
                    target = next(m for m in state["messages"] if m["id"] == pending["message_id"])
                    chat_service.archive_dashboard(
                        who, state, target, pending["query"], pending["tools"], progress
                    )
                return finish(
                    who,
                    state,
                    live["revision"],
                    refresh=False,
                    autosave=bool(state.get("workspace"))
                    and state.get("saved_dashboard_version") != state["dashboard"]["version"],
                )

    jobs = AnalysisJobs(store, run_job)
    app.state.jobs = jobs

    @app.post("/api/chat/jobs", status_code=202)
    def start_job(body: ChatRequest, who=Depends(owner)):
        return jobs.submit(who, body.model_dump())

    @app.get("/api/chat/jobs/active")
    def active_job(who=Depends(owner)):
        return {"job": jobs.active(who)}

    @app.get("/api/chat/jobs/{ident}")
    def get_job(ident: str, who=Depends(owner)):
        return jobs.get(who, ident)

    @app.post("/api/chat/action")
    def chat_action(body: ActionRequest, who=Depends(owner)):
        with locks[who]:
            state = current(who, body.revision)
            chat_service.action(who, state, body.action_id, body.message_id)
            operation = state["chat_actions"][body.action_id]["operation"]
            return finish(
                who, state, body.revision, autosave=operation in {"filters", "series", "zoom", "reset_zoom"}
            )

    @app.get("/api/chat/media/{ident}")
    def chat_media(ident: str, who=Depends(owner)):
        media = store.media(who, ident)
        disposition = "inline" if media["mime"] == "image/png" else "attachment"
        return Response(
            media["body"],
            media_type=media["mime"],
            headers={"Content-Disposition": f'{disposition}; filename="{media["filename"]}"'},
        )

    @app.post("/api/filters")
    def filters(body: Mutation, who=Depends(owner)):
        with locks[who]:
            return edit_card(who, body, "apply_filters")

    @app.post("/api/chart")
    def chart(body: Mutation, who=Depends(owner)):
        with locks[who]:
            return edit_card(who, body, "update_chart")

    @app.post("/api/calculation")
    def calculation(body: Mutation, who=Depends(owner)):
        with locks[who]:
            return edit_card(who, body, "compute_column")

    @app.post("/api/refresh")
    def refresh(body: Mutation, who=Depends(owner)):
        with locks[who]:
            state = current(who, body.revision)
            args = body.arguments or {
                "start": state["dataset"]["query_start"],
                "end": state["dataset"]["query_end"],
                "kind": state["dataset"]["kind"],
            }
            import copy

            old = copy.deepcopy(state)
            Tools(settings, store, who, state).invoke("fetch_data", args)
            state["calculations"] = old["calculations"]
            state["saved_id"] = old.get("saved_id")
            state["calculation_history"] = old["calculation_history"]
            state["filters"] = old["filters"]
            state["dashboard"] = old["dashboard"]
            state["dashboard"]["version"] += 1
            state["dataset"]["version"] = old["dataset"]["version"] + 1
            state["dashboard"]["table_id"] = state["dataset"]["id"]
            state["dashboard"]["table_version"] = state["dataset"]["version"]
            return finish(who, state, body.revision, autosave=True)

    @app.get("/api/dashboards")
    def dashboards(
        search: str = Query("", max_length=200),
        sort: Literal["updated", "created", "title"] = "updated",
        who=Depends(owner),
    ):
        return {"items": store.list(who, search, sort)}

    @app.post("/api/dashboards/save")
    def save(body: Mutation, who=Depends(owner)):
        with locks[who]:
            return edit_card(who, body, "save_dashboard")

    @app.post("/api/sync-zoom")
    def sync_zoom(body: Mutation, who=Depends(owner)):
        with locks[who]:
            return edit_card(who, body, "set_sync_zoom")

    @app.post("/api/dashboards/{ident}/open")
    def open_saved(ident: str, body: Mutation, who=Depends(owner)):
        with locks[who]:
            old = current(who, body.revision)
            state = chat_service.open_saved(who, old, ident)
            options = OpenSaved.model_validate(body.arguments)
            if options.as_new:
                title = state["dashboard"]["title"][:150] + " — копия"
                Tools(settings, store, who, state).invoke("save_dashboard", {"title": title, "as_new": True})
                message = state["messages"][-1]
                message["content"] = f"Создана копия дашборда «{title}»."
                chat_service.rich_message(message)
            return finish(who, state, body.revision, refresh=False)

    @app.post("/api/workspace/continue")
    def continue_card(body: Mutation, who=Depends(owner)):
        with locks[who]:
            old = current(who, body.revision)
            if not body.message_id:
                raise ValueError("Выберите карточку дашборда")
            state = store.card(who, old, body.message_id)
            state["messages"] = old["messages"]
            state["chat_actions"] = old.get("chat_actions", {})
            state["revision"] = old["revision"]
            entry = next((d for d in store.list(who) if d["id"] == state.get("saved_id")), None)
            select_workspace(state, body.message_id, entry)
            if settings.dashboard_brief_enabled:
                from shared.dashboard_brief import adopt_workspace

                adopt_workspace(state, old)
            return finish(who, state, body.revision, autosave=not bool(entry))

    @app.post("/api/workspace/new")
    def new_analysis(body: Mutation, who=Depends(owner)):
        with locks[who]:
            old = current(who, body.revision)
            store.prepare_cards(who, old)
            state = empty_state()
            state.update(
                messages=old["messages"], chat_actions=old.get("chat_actions", {}), revision=old["revision"]
            )
            if settings.dashboard_brief_enabled:
                from shared.dashboard_brief import adopt_workspace

                adopt_workspace(state, old)
            return finish(who, state, body.revision, refresh=False)

    @app.patch("/api/dashboards/{ident}")
    def rename(ident: str, body: Save, who=Depends(owner)):
        with locks[who]:
            renamed = store.rename(who, ident, body.title)
            state = store.load(who)
            if state["saved_id"] == ident:
                revision = state["revision"]
                state["dashboard"]["title"] = body.title
                if state.get("workspace"):
                    state["workspace"]["title"] = body.title
                    state["workspace"]["updated"] = renamed["updated"]
                store.commit(who, state, revision)
        return {"ok": True}

    @app.delete("/api/dashboards/{ident}")
    def delete(ident: str, who=Depends(owner)):
        with locks[who]:
            store.delete(who, ident)
            state = store.load(who)
            revision = state["revision"]
            if state.get("saved_id") == ident:
                state["saved_id"] = None
                state["workspace"] = None
            for message in state["messages"]:
                if message.get("dashboard", {}).get("saved_id") == ident:
                    message["dashboard"].update(saved_id=None, saved=False)
            store.commit(who, state, revision)
        return {"ok": True}

    @app.get("/api/export/{kind}")
    def export(
        kind: Literal["csv", "xlsx", "png", "html", "svg"],
        index: int = Query(1, ge=1, le=4),
        who=Depends(owner),
    ):
        state = store.load(who)
        if not state["dataset"]:
            raise ValueError("Нет таблицы для выгрузки")
        if kind == "csv":
            return Response(
                csv_export(state),
                media_type="text/csv",
                headers={"Content-Disposition": 'attachment; filename="puzzle-data.csv"'},
            )
        if kind == "html":
            return Response(
                html_export(state),
                media_type="text/html",
                headers={"Content-Disposition": 'attachment; filename="puzzle-dashboard.html"'},
            )
        if kind in {"xlsx", "png"}:
            return Response(
                xlsx_export(state) if kind == "xlsx" else png_export(state),
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                if kind == "xlsx"
                else "image/png",
                headers={
                    "Content-Disposition": f'attachment; filename="puzzle-{"data" if kind == "xlsx" else "dashboard"}.{kind}"'
                },
            )
        view = present(state)
        if index > len(view["charts"]):
            raise ValueError("График не найден")
        if not view["charts"][index - 1]["svg"]:
            raise ValueError("Недостаточно точек для экспорта графика")
        return Response(
            view["charts"][index - 1]["svg"],
            media_type="image/svg+xml",
            headers={"Content-Disposition": 'attachment; filename="puzzle-chart.svg"'},
        )

    from MY.library_routes import install_routes

    install_routes(app, store, settings, chat_service, owner, locks, current, finish, fit_previous_cards)
    from MY.card_routes import install_card_routes

    install_card_routes(app, store, settings, chat_service, owner, locks)
    return app


app = create_app()
