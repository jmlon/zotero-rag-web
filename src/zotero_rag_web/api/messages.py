"""Asking: POST a question, receive the answer as Server-Sent Events (PRD §6.3).

Events: `status` {stage}, `query` {standalone} for a follow-up, `token` {text}, then
`done` {message_id, status, answer, sources, usage, standalone} or `error` {code}.

Problems found before any cost (too long, limits, empty index, unknown conversation)
are ordinary JSON errors, not events. The answer is computed on a worker thread and
saved by it, so it is kept even if the asker leaves; leaving (closing the page,
pressing Stop) cancels the stream, and what was written so far is saved as `stopped`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from zotero_rag_core.llm import LLMError
from zotero_rag_core.querying import Turn
from zotero_rag_core.store import Filters
from zotero_rag_web import conversations, limits
from zotero_rag_web.api.deps import parse_id
from zotero_rag_web.api.errors import ApiError
from zotero_rag_web.chat.filters import SearchFilters, UnknownCollectionError, to_core
from zotero_rag_web.chat.service import (
    AnswerEngine,
    IndexNotReady,
    source_json,
    usage_json,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/conversations", tags=["messages"])

#: A comment line this often keeps proxies from closing a quiet connection.
HEARTBEAT_S = 15.0
_END = object()


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1)


@dataclass
class Prepared:
    question: str
    history: list[Turn]
    filters: Filters | None
    assistant_id: uuid.UUID


def _prepare(request: Request, conversation_id: uuid.UUID, question: str) -> Prepared:
    """Every check that can refuse the question, then record it. Runs on a thread."""
    app = request.app
    settings = app.state.settings
    if len(question) > settings.max_question_chars:
        raise ApiError(400, "question_too_long", max_chars=settings.max_question_chars)
    engine: AnswerEngine | None = app.state.engine
    if app.state.pool is None or engine is None:
        raise ApiError(503, "not_configured", "The server is missing its OpenRouter key.")
    browser_id: uuid.UUID = request.state.browser_id
    ip_hash = app.state.identity.ip_hash(request.client.host if request.client else None)
    with app.state.pool.connection() as conn:
        conn.autocommit = True
        conversations.touch_browser(conn, browser_id)
        conversation = conversations.get(conn, browser_id, conversation_id)
        if conversation is None:
            raise ApiError(404, "not_found")
        if conversations.message_count(conn, conversation_id) >= conversations.MAX_MESSAGES:
            raise ApiError(409, "conversation_full", "Start a new conversation.")
        try:
            limits.check(conn, settings, browser_id, ip_hash)
        except limits.LimitExceeded as exc:
            raise ApiError(429, exc.code, exc.message, retry_after_s=exc.retry_after_s) from exc
        try:
            engine.check_ready()
        except IndexNotReady as exc:
            raise ApiError(503, exc.code, str(exc)) from exc
        try:
            filters = to_core(SearchFilters.model_validate(conversation["filters"]), conn)
        except UnknownCollectionError as exc:
            raise ApiError(400, "unknown_collection", "Its collection left the library.") from exc
        history = conversations.history(conn, conversation_id, settings.history_turns)
        _, assistant_id = conversations.start_exchange(conn, conversation_id, question, ip_hash)
    return Prepared(question, history, filters, assistant_id)


@router.post("/{conversation_id}/messages")
async def ask(conversation_id: str, body: Question, request: Request) -> StreamingResponse:
    key = parse_id(conversation_id)
    question = body.question.strip()
    if not question:
        raise ApiError(422, "invalid_request", fields=["question"])
    prepared = await run_in_threadpool(_prepare, request, key, question)

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Any] = asyncio.Queue()
    cancelled = threading.Event()

    def emit(name: str, data: dict[str, Any]) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, (name, data))

    def work() -> None:
        try:
            _answer(request, prepared, emit, cancelled)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, _END)

    worker = loop.run_in_executor(None, work)
    request.app.state.workers.add(worker)
    worker.add_done_callback(request.app.state.workers.discard)

    async def events() -> AsyncIterator[str]:
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), HEARTBEAT_S)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if item is _END:
                    return
                name, data = item
                yield f"event: {name}\ndata: {json.dumps(data, default=str)}\n\n"
        finally:
            # Done, or the asker left: either way, stop forwarding tokens.
            cancelled.set()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _answer(request: Request, prepared: Prepared, emit: Any, cancelled: threading.Event) -> None:
    """Answer, save, report. Runs on a worker thread; never raises."""
    app = request.app
    settings = app.state.settings
    engine: AnswerEngine = app.state.engine
    snapshot = {
        "llm_model": settings.llm_model,
        "embedding_model": settings.embedding_model,
        "reranker": settings.reranker_name,
        "top_k": settings.top_k,
        "retrieval": settings.retrieval,
    }
    try:
        answer = engine.ask(prepared.question, prepared.history, prepared.filters, emit, cancelled)
    except LLMError:
        logger.exception("the language model failed")
        _save_failure(app, prepared.assistant_id, snapshot)
        emit("error", {"code": "llm_failed"})
        return
    except Exception:
        logger.exception("answering failed")
        _save_failure(app, prepared.assistant_id, snapshot)
        emit("error", {"code": "internal"})
        return

    status = "stopped" if answer.stopped else "done"
    text = answer.partial if answer.stopped else answer.result.answer
    sources = [source_json(s) for s in answer.result.sources if s.cited]
    usage = usage_json(answer)
    with app.state.pool.connection() as conn:
        conn.autocommit = True
        conversations.finish_exchange(
            conn,
            prepared.assistant_id,
            status=status,
            content=text,
            standalone_query=answer.standalone,
            sources=sources,
            usage=usage,
            settings=snapshot,
        )
    emit(
        "done",
        {
            "message_id": str(prepared.assistant_id),
            "status": status,
            "answer": text,
            "sources": sources,
            "usage": usage,
            "standalone": answer.standalone,
        },
    )


def _save_failure(app: Any, assistant_id: uuid.UUID, snapshot: dict[str, Any]) -> None:
    try:
        with app.state.pool.connection() as conn:
            conn.autocommit = True
            conversations.finish_exchange(
                conn, assistant_id, status="error", content="", settings=snapshot
            )
    except Exception:
        logger.exception("could not record the failed answer")
