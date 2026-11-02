"""The FastAPI application: the JSON API under /api, and the React app at /.

Run with `uvicorn zotero_rag_web.app:create_app --factory`.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from starlette.staticfiles import StaticFiles

from zotero_rag_web import __version__, logs
from zotero_rag_web.db import open_pool, ping
from zotero_rag_web.settings import Settings

logger = logging.getLogger(__name__)
access_logger = logging.getLogger("zotero_rag_web.access")

REQUEST_ID_HEADER = "X-Request-ID"
#: The deployment is reachable by URL, not meant to be found (PRD §8.1).
ROBOTS_HEADER = ("X-Robots-Tag", "noindex, nofollow")
ROBOTS_TXT = "User-agent: *\nDisallow: /\n"

#: Shown at / when the React app has not been built (`npm run build` in web/).
NOT_BUILT_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Zotero RAG</title></head>
<body><h1>Zotero RAG</h1>
<p>The web app has not been built. Run <code>npm run build</code> in <code>web/</code>,
or use the Docker image.</p></body></html>"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    logs.configure(settings.log_level, settings.log_format)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pool = open_pool(settings.database_url) if settings.database_url else None
        if pool is not None:
            # Opened without waiting: a database that is still starting must not
            # stop the web server from starting (see `db.open_pool`).
            pool.open(wait=False)
        app.state.pool = pool
        logger.info("started", extra={"version": __version__})
        try:
            yield
        finally:
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

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """A request id for every request (kept if the platform's proxy sent one),
        echoed in the response and attached to every log line, and `noindex`."""
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        current = incoming if 0 < len(incoming) <= 64 else uuid.uuid4().hex
        token = logs.request_id.set(current)
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
        finally:
            # The access line, written here so it carries the request id
            # (uvicorn's own access log runs with --no-access-log).
            access_logger.info(
                "%s %s %d",
                request.method,
                request.url.path,
                status,
                extra={"duration_ms": round((time.perf_counter() - start) * 1000, 1)},
            )
            logs.request_id.reset(token)
        response.headers[REQUEST_ID_HEADER] = current
        response.headers[ROBOTS_HEADER[0]] = ROBOTS_HEADER[1]
        return response

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
