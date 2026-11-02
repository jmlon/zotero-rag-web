"""The FastAPI application: the JSON API under /api, and the React app at /.

Run with `uvicorn zotero_rag_web.app:create_app --factory`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from starlette.staticfiles import StaticFiles

from zotero_rag_web import __version__, logs
from zotero_rag_web.api import conversations, errors, library, messages
from zotero_rag_web.chat.service import AnswerEngine
from zotero_rag_web.db import open_pool, ping
from zotero_rag_web.identity import Identity
from zotero_rag_web.middleware import RequestContext
from zotero_rag_web.settings import Settings

logger = logging.getLogger(__name__)

#: The deployment is reachable by URL, not meant to be found (PRD §8.1).
ROBOTS_TXT = "User-agent: *\nDisallow: /\n"

#: Shown at / when the React app has not been built (`npm run build` in web/).
NOT_BUILT_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Zotero RAG</title></head>
<body><h1>Zotero RAG</h1>
<p>The web app has not been built. Run <code>npm run build</code> in <code>web/</code>,
or use the Docker image.</p></body></html>"""


def create_app(settings: Settings | None = None, engine: AnswerEngine | None = None) -> FastAPI:
    """The app. `engine` replaces the answering pipeline (tests); by default it is
    built at startup when the OpenRouter key is set."""
    settings = settings or Settings()
    logs.configure(settings.log_level, settings.log_format)
    identity = Identity(settings.secret_key.get_secret_value() if settings.secret_key else None)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pool = open_pool(settings.database_url) if settings.database_url else None
        if pool is not None:
            # Opened without waiting: a database that is still starting must not
            # stop the web server from starting (see `db.open_pool`).
            pool.open(wait=False)
        app.state.pool = pool
        app.state.engine = engine or _build_engine(settings, pool)
        logger.info("started", extra={"version": __version__})
        try:
            yield
        finally:
            # Answers still being written finish and are saved before the pool closes.
            if app.state.workers:
                await asyncio.wait(list(app.state.workers), timeout=30)
            if pool is not None:
                pool.close()

    app = FastAPI(
        title="zotero-rag-web",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.identity = identity
    app.state.workers = set()
    app.add_middleware(RequestContext, identity=identity, cookie_secure=settings.cookie_secure)
    errors.install(app)
    app.include_router(library.router)
    app.include_router(conversations.router)
    app.include_router(messages.router)

    @app.get("/healthz", include_in_schema=False)
    def healthz(request: Request) -> JSONResponse:
        """Up, and whether the database answers. 503 when it does not, so the
        platform's health check holds a deploy back from a broken database."""
        pool = request.app.state.pool
        database = "ok" if pool is not None and ping(pool) else "unreachable"
        if pool is None:
            database = "not configured"
        status = 200 if database == "ok" else 503
        return JSONResponse(
            {
                "status": "ok" if status == 200 else "degraded",
                "database": database,
                "version": __version__,
            },
            status_code=status,
        )

    @app.get("/robots.txt", include_in_schema=False)
    def robots() -> PlainTextResponse:
        return PlainTextResponse(ROBOTS_TXT)

    @app.api_route(
        "/api/{path:path}",
        methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        include_in_schema=False,
    )
    def unknown_api(path: str) -> JSONResponse:
        """An unknown API path is a JSON 404, never the React app's HTML."""
        return JSONResponse({"error": {"code": "not_found"}}, status_code=404)

    _mount_spa(app, settings.static_dir)
    return app


def _mount_spa(app: FastAPI, static_dir: Path) -> None:
    """Serve the built React app: its files, and index.html for any other path, so
    the client-side router can handle deep links such as /c/<conversation>."""
    index = static_dir / "index.html"
    assets = static_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> Response:
        if not index.is_file():
            return HTMLResponse(NOT_BUILT_PAGE)
        candidate = (static_dir / path).resolve()
        # A top-level file of the build (favicon.svg ...), never anything outside it.
        if path and candidate.is_file() and candidate.parent == static_dir.resolve():
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


def _build_engine(settings: Settings, pool: Any) -> AnswerEngine | None:
    """The answering pipeline, or None (asking answers 503) until the key is set."""
    if pool is None or settings.openrouter_api_key is None:
        logger.warning("no OPENROUTER_API_KEY or database: questions cannot be answered")
        return None
    return AnswerEngine.from_settings(settings, pool)
