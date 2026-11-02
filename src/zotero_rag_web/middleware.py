"""Per-request context, as plain ASGI middleware.

Not `@app.middleware("http")`: Starlette's BaseHTTPMiddleware wraps the response
body, which gets in the way of streamed answers and of noticing that the client
went away mid-stream, which is how Stop works (M3). Plain ASGI passes both through.

For every request: a request id (kept if the proxy sent one), echoed in the
response and attached to every log line; `X-Robots-Tag`; one access log line with
the final status. For /api: the browser id from the signed cookie, issuing one
when it is missing or forged.
"""

from __future__ import annotations

import logging
import time
import uuid
from http.cookies import SimpleCookie

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from zotero_rag_web import logs
from zotero_rag_web.identity import COOKIE_MAX_AGE, COOKIE_NAME, Identity

access_logger = logging.getLogger("zotero_rag_web.access")

REQUEST_ID_HEADER = "x-request-id"
ROBOTS = (b"x-robots-tag", b"noindex, nofollow")


class RequestContext:
    def __init__(self, app: ASGIApp, identity: Identity, cookie_secure: bool) -> None:
        self.app = app
        self.identity = identity
        self.cookie_secure = cookie_secure

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        incoming = headers.get(REQUEST_ID_HEADER, "")
        request_id = incoming if 0 < len(incoming) <= 64 else uuid.uuid4().hex

        new_cookie: str | None = None
        if scope["path"].startswith("/api/"):
            jar: SimpleCookie = SimpleCookie()
            jar.load(headers.get("cookie", ""))
            morsel = jar.get(COOKIE_NAME)
            browser_id = self.identity.read(morsel.value if morsel else None)
            if browser_id is None:
                browser_id = uuid.uuid4()
                new_cookie = self.identity.issue(browser_id)
            scope.setdefault("state", {})["browser_id"] = browser_id

        status = 500
        start = time.perf_counter()

        async def send_with_context(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                extra: list[tuple[bytes, bytes]] = [
                    (REQUEST_ID_HEADER.encode(), request_id.encode()),
                    ROBOTS,
                ]
                if new_cookie is not None:
                    extra.append((b"set-cookie", self._cookie(new_cookie).encode("latin-1")))
                message["headers"] = [*message.get("headers", []), *extra]
            await send(message)

        token = logs.request_id.set(request_id)
        try:
            await self.app(scope, receive, send_with_context)
        finally:
            access_logger.info(
                "%s %s %d",
                scope["method"],
                scope["path"],
                status,
                extra={"duration_ms": round((time.perf_counter() - start) * 1000, 1)},
            )
            logs.request_id.reset(token)

    def _cookie(self, value: str) -> str:
        parts = [
            f"{COOKIE_NAME}={value}",
            "Path=/",
            f"Max-Age={COOKIE_MAX_AGE}",
            "HttpOnly",
            "SameSite=Lax",
        ]
        if self.cookie_secure:
            parts.append("Secure")
        return "; ".join(parts)
