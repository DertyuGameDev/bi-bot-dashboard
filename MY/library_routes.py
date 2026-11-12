"""Dashboard actions kept separate from conversation handling."""

import copy
from typing import Literal
from fastapi import Depends, Query, Response
from pydantic import Field
from shared.models import Strict
from shared.analytics import present
from shared.dashboard_context import active_message
from shared.exports import csv_export, xlsx_export, png_export, html_export
from MY.public_access import public_base


class Operation(Strict):
    revision: int = Field(ge=0)
    arguments: dict = Field(default_factory=dict)
    message_id: str | None = Field(None, max_length=64)


class Draft(Strict):
    context: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    text: str = Field(max_length=4000)
    version: int = Field(0, ge=0, le=9007199254740991)


class Template(Strict):
    title: str = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=1, max_length=4000)


def install_routes(app, store, settings, chat, owner, locks, current, finish, fit_previous_cards):
    def scope(who, message_id=None, session=None):
        state = session or store.load(who)
        result = store.card(who, state, message_id) if message_id else state
        if not result.get("dataset"):
            raise ValueError("Нет дашборда для этого действия")
        return result

    def publication_info(who, ident):
        base = public_base(settings)
        published = store.publication(who, ident) if ident else None
        return {
            "available": bool(base),
            "temporary": bool(base) and not bool(settings.public_base_url),
            "published": {**published, "url": base + "/s/" + published["token"] if base else None}
            if published
            else None,
        }

    @app.get("/api/card-actions")
    def actions(message_id: str | None = Query(None, max_length=64), who=Depends(owner)):
        state = scope(who, message_id)
        entry = next((d for d in store.list(who) if d["id"] == state.get("saved_id")), None)
        return {
            "saved_id": state.get("saved_id"),
            "title": state["dashboard"]["title"],
            "query": entry["query"] if entry else "",
            "publication": publication_info(who, state.get("saved_id")),
        }

    @app.get("/api/download/{kind}")
    def download(
        kind: Literal["csv", "xlsx", "html", "png"],
        message_id: str | None = Query(None, max_length=64),
        who=Depends(owner),
    ):
        state = scope(who, message_id)
        renderers = {
            "csv": (csv_export, "text/csv", "puzzle-data.csv"),
            "xlsx": (
                xlsx_export,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "puzzle-data.xlsx",
            ),
            "html": (html_export, "text/html", "puzzle-dashboard.html"),
            "png": (png_export, "image/png", "puzzle-dashboard.png"),
        }
        renderer, mime, filename = renderers[kind]
        return Response(
            renderer(state),
            media_type=mime,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/dashboards/{ident}/versions")
    def versions(ident: str, who=Depends(owner)):
        return {"items": store.versions(who, ident)}

    @app.post("/api/dashboards/{ident}/versions/{seq}/restore")
    def restore(ident: str, seq: int, body: Operation, who=Depends(owner)):
        with locks[who]:
            session = current(who, body.revision)
            target = scope(who, body.message_id, session)
            if target.get("saved_id") != ident or not body.message_id:
                raise ValueError("Выберите карточку этого дашборда")
            version = store.version(who, ident, seq)
            version["dashboard"]["version"] = target["dashboard"]["version"] + 1
            version["_version_label"] = f"Восстановлена версия {seq}"
            message = next(m for m in session["messages"] if m.get("id") == body.message_id)
            active = active_message(session)
            if active is message:
                for key in [
                    "dataset",
                    "snapshot",
                    "filters",
                    "dashboard",
                    "calculations",
                    "calculation_history",
                    "saved_id",
                    "filter_notice",
                    "_version_label",
                ]:
                    session[key] = version.get(key)
                if session.get("workspace"):
                    session["workspace"]["title"] = version["dashboard"]["title"]
                chat.dashboard(who, session, message)
                return finish(who, session, body.revision, refresh=False, autosave=True)
            version["messages"] = [copy.deepcopy(message)]
            chat.dashboard(who, version, version["messages"][-1])
            present(version)
            store.commit(
                who,
                session,
                body.revision,
                card_update=(body.message_id, version, {"title": version["dashboard"]["title"]}),
            )
            fit_previous_cards(who, session)
            return present(session)

    @app.get("/api/draft")
    def draft(context: str = Query("conversation", pattern=r"^[A-Za-z0-9_-]{1,64}$"), who=Depends(owner)):
        return store.draft(who, context)

    @app.post("/api/draft")
    def save_draft(body: Draft, who=Depends(owner)):
        return store.save_draft(who, body.context, body.text, body.version)

    @app.get("/api/templates")
    def templates(who=Depends(owner)):
        return {"items": store.templates(store.account_owner(who))}

    @app.post("/api/templates")
    def create_template(body: Template, who=Depends(owner)):
        return store.save_template(store.account_owner(who), body.title, body.prompt)

    @app.patch("/api/templates/{ident}")
    def change_template(ident: str, body: Template, who=Depends(owner)):
        return store.save_template(store.account_owner(who), body.title, body.prompt, ident)

    @app.delete("/api/templates/{ident}")
    def remove_template(ident: str, who=Depends(owner)):
        store.delete_template(store.account_owner(who), ident)
        return {"ok": True}

    @app.post("/api/publications")
    def publish(body: Operation, who=Depends(owner)):
        with locks[who]:
            session = current(who, body.revision)
            state = scope(who, body.message_id, session)
            if not public_base(settings):
                raise ValueError("Сервис внешних ссылок не запущен")
            store.publish(who, state["saved_id"], state["dashboard"]["title"], html_export(state))
            return publication_info(who, state["saved_id"])

    @app.delete("/api/publications/{ident}")
    def revoke(ident: str, who=Depends(owner)):
        # Missing/foreign ids reveal nothing and cannot revoke someone else's publication.
        store.revoke(who, ident)
        return {"ok": True}
