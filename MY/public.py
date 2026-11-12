"""Separate read-only origin: no sessions, chat, database tools or owner APIs."""

import re
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from shared.config import Settings
from shared.storage import Store


def create_public_app(settings=None):
    settings = settings or Settings()
    store = Store(settings.storage)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'self'; base-uri 'none'; form-action 'none'"
        )
        return response

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/s/{token}", response_class=HTMLResponse)
    def snapshot(token: str):
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,80}", token):
            raise HTTPException(404, "Публикация не найдена или отозвана")
        try:
            return HTMLResponse(store.public_snapshot(token))
        except ValueError:
            raise HTTPException(404, "Публикация не найдена или отозвана") from None

    return app


app = create_public_app()
