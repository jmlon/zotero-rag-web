"""The database: connection URLs, the connection pool, and the web app's own tables.

The chunk store's tables (items, attachments, chunks, chunk_store, term_df) belong
to zotero-rag-core and are created from its `pg_schema.sql` (migration 0001). The
tables below are the web app's: the library and its collections, index runs, and
conversations. `migrations/` creates them; this metadata is what the code queries,
and a test checks that the two agree.
"""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg_pool import ConnectionPool, PoolTimeout
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    SmallInteger,
    Table,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

_SCHEMES = ("postgresql://", "postgres://", "postgresql+psycopg://")


def libpq_url(url: str) -> str:
    """The URL as psycopg takes it: `postgresql://...`, whatever the platform wrote."""
    for scheme in _SCHEMES:
        if url.startswith(scheme):
            return "postgresql://" + url[len(scheme) :]
    raise ValueError("DATABASE_URL must be a PostgreSQL URL (postgresql://user:pass@host/db)")


def sqlalchemy_url(url: str) -> str:
    """The URL as SQLAlchemy (and so Alembic) takes it, with the psycopg 3 driver."""
    return "postgresql+psycopg://" + libpq_url(url).removeprefix("postgresql://")


def open_pool(url: str, *, max_size: int = 10, timeout: float = 10.0) -> ConnectionPool[Any]:
    """A pool of connections, opened lazily: the app starts even while the database is down.

    `/healthz` then reports the database as unreachable instead of the process
    crashing in a restart loop, which is easier to diagnose on a hosted platform.
    """
    return ConnectionPool(
        libpq_url(url), min_size=1, max_size=max_size, timeout=timeout, open=False
    )


def ping(pool: ConnectionPool[Any]) -> bool:
    """Whether the database answers a trivial query now."""
    try:
        with pool.connection(timeout=3) as conn:
            conn.execute("SELECT 1")
        return True
    except (psycopg.Error, PoolTimeout, OSError):
        return False


metadata = MetaData()

#: The one library this deployment serves, and the incremental sync's cursor.
library = Table(
    "library",
    metadata,
    Column("id", SmallInteger, primary_key=True, server_default=text("1")),
    Column("group_id", BigInteger),
    Column("name", Text),
    Column("web_url", Text),
    Column("last_version", BigInteger),
    Column("last_sync_at", DateTime(timezone=True)),
    Column("last_sync_status", Text),
    CheckConstraint("id = 1", name="library_single_row"),
)

#: The group's collection tree, rewritten by every sync.
collections = Table(
    "collections",
    metadata,
    Column("key", Text, primary_key=True),
    Column("parent_key", Text),
    Column("name", Text, nullable=False),
    Column("path", Text),
    Column("item_count", Integer, nullable=False, server_default=text("0")),
)

index_runs = Table(
    "index_runs",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("started_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("finished_at", DateTime(timezone=True)),
    Column("trigger", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("stats", JSONB),
    Column("failures", JSONB),
)

#: An anonymous browser (PRD §8.2); `user_id` once sign-in exists (§11.2).
browsers = Table(
    "browsers",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("last_seen_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("user_id", UUID(as_uuid=True)),
)

conversations = Table(
    "conversations",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column(
        "browser_id",
        UUID(as_uuid=True),
        ForeignKey("browsers.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("title", Text, nullable=False, server_default=text("''")),
    Column("filters", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Index("conversations_browser_updated_idx", "browser_id", "updated_at"),
)

messages = Table(
    "messages",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column(
        "conversation_id",
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("role", Text, nullable=False),
    Column("content", Text, nullable=False, server_default=text("''")),
    Column("standalone_query", Text),
    Column("sources", JSONB),
    Column("usage", JSONB),
    Column("settings", JSONB),
    Column("status", Text, nullable=False, server_default=text("'done'")),
    #: Salted hash of the asker's IP, for the per-IP rate limit; never the raw IP.
    Column("ip_hash", Text),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("role IN ('user', 'assistant', 'event')", name="messages_role"),
    Index("messages_conversation_created_idx", "conversation_id", "created_at"),
    Index("messages_created_idx", "created_at"),
)

#: Columns the web app adds to the core's tables, by table.
CORE_TABLE_ADDITIONS: dict[str, list[Column[Any]]] = {
    "items": [
        Column("version", BigInteger),
        Column("date", Text),
        Column("updated_at", DateTime(timezone=True)),
    ],
    "attachments": [
        Column("content_type", Text),
        Column("status", Text),
        Column("error", Text),
        Column("indexed_at", DateTime(timezone=True)),
    ],
}

#: Tables created by the core's schema, not by this app's metadata.
CORE_TABLES = ("chunk_store", "items", "attachments", "chunks", "term_df")
