"""Conversations: list, create, read, rename or re-filter, delete (PRD §6.3)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field

from zotero_rag_web import conversations as store
from zotero_rag_web.api.deps import Browser, Connection, parse_id
from zotero_rag_web.api.errors import ApiError
from zotero_rag_web.chat.filters import SearchFilters, UnknownCollectionError, to_core

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


class NewConversation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filters: SearchFilters = Field(default_factory=SearchFilters)


class ConversationChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, max_length=200)
    filters: SearchFilters | None = None


def _checked_filters(conn: Any, filters: SearchFilters) -> dict[str, Any]:
    """Filters as stored, after checking the collection still exists."""
    try:
        to_core(filters, conn)
    except UnknownCollectionError as exc:
        raise ApiError(400, "unknown_collection", "That collection is not in the library.") from exc
    return filters.model_dump(exclude_none=True)


@router.get("")
def list_conversations(conn: Connection, browser_id: Browser) -> list[dict[str, Any]]:
    return store.list_for(conn, browser_id)


@router.post("", status_code=201)
def create_conversation(
    body: NewConversation, conn: Connection, browser_id: Browser
) -> dict[str, Any]:
    return store.create(conn, browser_id, _checked_filters(conn, body.filters))


@router.get("/{conversation_id}")
def read_conversation(
    conversation_id: str, conn: Connection, browser_id: Browser
) -> dict[str, Any]:
    key = parse_id(conversation_id)
    conversation = store.get(conn, browser_id, key)
    if conversation is None:
        raise ApiError(404, "not_found")
    return conversation | {"messages": store.messages(conn, key)}


@router.patch("/{conversation_id}")
def change_conversation(
    conversation_id: str, body: ConversationChange, conn: Connection, browser_id: Browser
) -> dict[str, Any]:
    filters = _checked_filters(conn, body.filters) if body.filters is not None else None
    changed = store.update(
        conn, browser_id, parse_id(conversation_id), title=body.title, filters=filters
    )
    if changed is None:
        raise ApiError(404, "not_found")
    return changed


@router.delete("/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: str, conn: Connection, browser_id: Browser) -> Response:
    if not store.delete(conn, browser_id, parse_id(conversation_id)):
        raise ApiError(404, "not_found")
    return Response(status_code=204)
