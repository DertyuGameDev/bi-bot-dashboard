"""Small, independently versioned card requests; never wait for the model lock."""

from collections import defaultdict
import copy
import threading
from typing import Literal
from fastapi import Depends, Query
from pydantic import Field
from shared.models import Strict
from shared.storage import Conflict
from shared.tools import Tools
from shared.analytics import present
from shared.dashboard_context import active_message


class CardEdit(Strict):
    settings_revision: int = Field(ge=0)
    operation: Literal["filters", "chart", "calculation", "sync"] = "filters"
    arguments: dict = Field(default_factory=dict)


def install_card_routes(app, store, settings, chat, owner, locks):
    card_locks = defaultdict(threading.RLock)

    def response(who, session, scope, ident, current=False):
        message = scope["messages"][-1]
        result = {
            "revision": session["revision"],
            "message": message,
            "settings_revision": store.card_revision(who, ident),
            "current": None,
        }
        message["dashboard"]["settings_revision"] = result["settings_revision"]
        if current:
            value = present(scope, False)
            if session.get("dashboard_brief"):
                from shared.dashboard_brief import context as brief_context

                value["debug"]["dashboard_brief"] = brief_context(session)
            value.pop("messages", None)
            value.pop("revision", None)
            value.pop("workspace", None)
            value["charts"] = message["dashboard"]["charts"]
            result["current"] = value
        return result

    @app.get("/api/cards/{ident}")
    def read(ident: str, who=Depends(owner)):
        with card_locks[who, ident]:
            session = store.load(who, hydrate=False)
            scope = store.card(who, session, ident)
            chat.dashboard(who, scope, scope["messages"][-1])
            return response(who, session, scope, ident)

    @app.get("/api/cards/{ident}/table")
    def table(
        ident: str,
        offset: int = Query(0, ge=0),
        limit: int = Query(25, ge=1, le=200),
        who=Depends(owner),
    ):
        with card_locks[who, ident]:
            session = store.load(who, hydrate=False)
            scope = store.card(who, session, ident)
            value = present(scope, False, offset, limit)
            dataset = value["dataset"]
            if dataset and offset >= dataset["rows"] and offset:
                offset = max(0, (dataset["rows"] - 1) // limit * limit)
                value = present(scope, False, offset, limit)
            return {
                "message_id": ident,
                "title": scope["dashboard"]["title"],
                "settings_revision": store.card_revision(who, ident),
                "can_undo": bool(scope.get("calculation_history")),
                **{key: value[key] for key in ["dataset", "filters", "calculations"]},
            }

    @app.post("/api/cards/{ident}/edit")
    def edit(ident: str, body: CardEdit, who=Depends(owner)):
        with card_locks[who, ident]:
            session = store.load(who, hydrate=False)
            scope = store.card(who, session, ident)
            if store.card_revision(who, ident) != body.settings_revision:
                raise Conflict(
                    "Настройки этого дашборда уже изменились. Обновите карточку и повторите действие."
                )
            operation = {
                "filters": "apply_filters",
                "chart": "update_chart",
                "calculation": "compute_column",
                "sync": "set_sync_zoom",
            }[body.operation]
            Tools(settings, store, who, scope).invoke(
                operation, {"filters": body.arguments} if body.operation == "filters" else body.arguments
            )
            chat.dashboard(who, scope, scope["messages"][-1])
            with locks[who]:
                session = store.load(who, hydrate=False)
                if store.card_revision(who, ident) != body.settings_revision:
                    raise Conflict("Настройки этого дашборда уже изменились. Повторите действие.")
                # Attach to the newest conversation, including any newly finished reply.
                if not any(m.get("id") == ident for m in session["messages"]):
                    raise ValueError("Дашборд больше не доступен в переписке")
                active = active_message(session)
                is_current = bool(active and active["id"] == ident)
                scope["messages"] = [
                    {
                        **copy.deepcopy(next(m for m in session["messages"] if m.get("id") == ident)),
                        "dashboard": scope["messages"][-1]["dashboard"],
                    }
                ]
                store.commit(
                    who,
                    session,
                    session["revision"],
                    card_update=(
                        ident,
                        scope,
                        {"title": scope["dashboard"]["title"], "update_caption": False},
                    ),
                    sync_card=is_current,
                )
            return response(who, session, scope, ident, is_current)
