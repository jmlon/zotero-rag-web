"""Conversations and their messages, always scoped to the asking browser.

Every query takes the browser id: one browser can never read or change another's
conversation, and asking for it is indistinguishable from asking for one that does
not exist (404, not 403).
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from zotero_rag_core.querying import Turn

#: A conversation holds at most this many messages (PRD §8.3).
MAX_MESSAGES = 200
TITLE_CHARS = 60


def touch_browser(conn: psycopg.Connection[Any], browser_id: uuid.UUID) -> None:
    conn.execute(
        "INSERT INTO browsers (id) VALUES (%s) ON CONFLICT (id) DO UPDATE SET last_seen_at = now()",
        (browser_id,),
    )


def list_for(conn: psycopg.Connection[Any], browser_id: uuid.UUID) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT id, title, created_at, updated_at FROM conversations "
            "WHERE browser_id = %s ORDER BY updated_at DESC LIMIT 500",
            (browser_id,),
        )
        return cur.fetchall()


def create(
    conn: psycopg.Connection[Any], browser_id: uuid.UUID, filters: dict[str, Any]
) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "INSERT INTO conversations (id, browser_id, filters) VALUES (%s, %s, %s) "
            "RETURNING id, title, filters, created_at, updated_at",
            (uuid.uuid4(), browser_id, Jsonb(filters)),
        )
        row = cur.fetchone()
    assert row is not None
    return row


def get(
    conn: psycopg.Connection[Any], browser_id: uuid.UUID, conversation_id: uuid.UUID
) -> dict[str, Any] | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT id, title, filters, created_at, updated_at FROM conversations "
            "WHERE id = %s AND browser_id = %s",
            (conversation_id, browser_id),
        )
        return cur.fetchone()


def messages(conn: psycopg.Connection[Any], conversation_id: uuid.UUID) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT id, role, content, standalone_query, sources, usage, status, created_at "
            "FROM messages WHERE conversation_id = %s ORDER BY created_at, id",
            (conversation_id,),
        )
        return cur.fetchall()


def update(
    conn: psycopg.Connection[Any],
    browser_id: uuid.UUID,
    conversation_id: uuid.UUID,
    *,
    title: str | None = None,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Rename and/or re-filter. A filter change is also recorded in the thread, as an
    `event` message, so the reader sees where the search changed (PRD §4.3)."""
    current = get(conn, browser_id, conversation_id)
    if current is None:
        return None
    if title is not None:
        conn.execute(
            "UPDATE conversations SET title = %s WHERE id = %s",
            (title.strip()[:200], conversation_id),
        )
    if filters is not None and filters != current["filters"]:
        conn.execute(
            "UPDATE conversations SET filters = %s, updated_at = now() WHERE id = %s",
            (Jsonb(filters), conversation_id),
        )
        conn.execute(
            "INSERT INTO messages (id, conversation_id, role, content, settings) "
            "VALUES (%s, %s, 'event', 'filters_changed', %s)",
            (uuid.uuid4(), conversation_id, Jsonb({"filters": filters})),
        )
    return get(conn, browser_id, conversation_id)


def delete(
    conn: psycopg.Connection[Any], browser_id: uuid.UUID, conversation_id: uuid.UUID
) -> bool:
    cursor = conn.execute(
        "DELETE FROM conversations WHERE id = %s AND browser_id = %s",
        (conversation_id, browser_id),
    )
    return cursor.rowcount > 0


def message_count(conn: psycopg.Connection[Any], conversation_id: uuid.UUID) -> int:
    row = conn.execute(
        "SELECT count(*) FROM messages WHERE conversation_id = %s", (conversation_id,)
    ).fetchone()
    return int(row[0]) if row else 0


def history(conn: psycopg.Connection[Any], conversation_id: uuid.UUID, turns: int) -> list[Turn]:
    """The last `turns` complete exchanges: a question and the answer it got.

    A stopped or failed answer is left out: half an answer is a poor context.
    """
    if turns <= 0:
        return []
    rows = conn.execute(
        "SELECT role, content, status FROM messages WHERE conversation_id = %s "
        "AND role IN ('user', 'assistant') ORDER BY created_at, id",
        (conversation_id,),
    ).fetchall()
    exchanges: list[Turn] = []
    question: str | None = None
    for role, content, status in rows:
        if role == "user":
            question = content
        elif question is not None:
            if status == "done":
                exchanges.append(Turn(question=question, answer=content))
            question = None
    return exchanges[-turns:]


def start_exchange(
    conn: psycopg.Connection[Any],
    conversation_id: uuid.UUID,
    question: str,
    ip_hash: str | None,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Record the question and an empty answer `streaming`; title a new conversation."""
    user_id, assistant_id = uuid.uuid4(), uuid.uuid4()
    conn.execute(
        "INSERT INTO messages (id, conversation_id, role, content, ip_hash) "
        "VALUES (%s, %s, 'user', %s, %s)",
        (user_id, conversation_id, question, ip_hash),
    )
    conn.execute(
        "INSERT INTO messages (id, conversation_id, role, status, created_at) "
        "VALUES (%s, %s, 'assistant', 'streaming', clock_timestamp())",
        (assistant_id, conversation_id),
    )
    conn.execute(
        "UPDATE conversations SET updated_at = now(), "
        "title = CASE WHEN title = '' THEN %s ELSE title END WHERE id = %s",
        (_title(question), conversation_id),
    )
    return user_id, assistant_id


def finish_exchange(
    conn: psycopg.Connection[Any],
    assistant_id: uuid.UUID,
    *,
    status: str,
    content: str,
    standalone_query: str | None = None,
    sources: list[dict[str, Any]] | None = None,
    usage: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        "UPDATE messages SET status = %s, content = %s, standalone_query = %s, "
        "sources = %s, usage = %s, settings = %s WHERE id = %s",
        (
            status,
            content,
            standalone_query,
            Jsonb(sources) if sources is not None else None,
            Jsonb(usage) if usage is not None else None,
            Jsonb(settings) if settings is not None else None,
            assistant_id,
        ),
    )


def _title(question: str) -> str:
    flat = " ".join(question.split())
    return flat if len(flat) <= TITLE_CHARS else flat[: TITLE_CHARS - 1].rstrip() + "…"
