"""What every API route needs: a database connection and the asking browser."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Annotated, Any

import psycopg
from fastapi import Depends, Request

from zotero_rag_web import conversations
from zotero_rag_web.api.errors import ApiError


def connection(request: Request) -> Iterator[psycopg.Connection[Any]]:
    pool = request.app.state.pool
    if pool is None:
        raise ApiError(503, "not_configured", "The server has no database configured.")
    with pool.connection() as conn:
        conn.autocommit = True
        yield conn


Connection = Annotated[psycopg.Connection[Any], Depends(connection)]


def browser(request: Request, conn: Connection) -> uuid.UUID:
    """The browser id the identity middleware attached, recorded as seen."""
    browser_id: uuid.UUID = request.state.browser_id
    conversations.touch_browser(conn, browser_id)
    return browser_id


Browser = Annotated[uuid.UUID, Depends(browser)]


def parse_id(value: str) -> uuid.UUID:
    """A conversation id from the path; anything malformed is simply not found."""
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise ApiError(404, "not_found") from exc
