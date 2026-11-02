"""The chunk store (zotero-rag-core's schema) and the web app's own tables.

Revision ID: 0001
Revises:
Create Date: 2026-11-01

The core's `pg_schema.sql` is idempotent, so a later core release that adds to it is
taken up by a new migration that runs it again (`op.execute(schema_sql())`). It is
read from the installed core, 0.5.1 or later.
"""

from __future__ import annotations

from alembic import op

from zotero_rag_core.stores.pg import schema_sql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

WEB_SCHEMA = """
ALTER TABLE items ADD COLUMN IF NOT EXISTS version bigint;
ALTER TABLE items ADD COLUMN IF NOT EXISTS date text;
ALTER TABLE items ADD COLUMN IF NOT EXISTS updated_at timestamptz;

ALTER TABLE attachments ADD COLUMN IF NOT EXISTS content_type text;
ALTER TABLE attachments ADD COLUMN IF NOT EXISTS status text;
ALTER TABLE attachments ADD COLUMN IF NOT EXISTS error text;
ALTER TABLE attachments ADD COLUMN IF NOT EXISTS indexed_at timestamptz;

CREATE TABLE library (
    id               smallint PRIMARY KEY DEFAULT 1,
    group_id         bigint,
    name             text,
    web_url          text,
    last_version     bigint,
    last_sync_at     timestamptz,
    last_sync_status text,
    CONSTRAINT library_single_row CHECK (id = 1)
);

CREATE TABLE collections (
    key        text PRIMARY KEY,
    parent_key text,
    name       text    NOT NULL,
    path       text,
    item_count integer NOT NULL DEFAULT 0
);

CREATE TABLE index_runs (
    id          bigserial   PRIMARY KEY,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    trigger     text        NOT NULL,
    status      text        NOT NULL,
    stats       jsonb,
    failures    jsonb
);

CREATE TABLE browsers (
    id           uuid        PRIMARY KEY,
    created_at   timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    user_id      uuid
);

CREATE TABLE conversations (
    id         uuid        PRIMARY KEY,
    browser_id uuid        NOT NULL REFERENCES browsers (id) ON DELETE CASCADE,
    title      text        NOT NULL DEFAULT '',
    filters    jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX conversations_browser_updated_idx ON conversations (browser_id, updated_at);

CREATE TABLE messages (
    id               uuid        PRIMARY KEY,
    conversation_id  uuid        NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    role             text        NOT NULL,
    content          text        NOT NULL DEFAULT '',
    standalone_query text,
    sources          jsonb,
    usage            jsonb,
    settings         jsonb,
    status           text        NOT NULL DEFAULT 'done',
    ip_hash          text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT messages_role CHECK (role IN ('user', 'assistant', 'event'))
);
CREATE INDEX messages_conversation_created_idx ON messages (conversation_id, created_at);
CREATE INDEX messages_created_idx ON messages (created_at);
"""


def upgrade() -> None:
    op.execute(schema_sql())
    op.execute(WEB_SCHEMA)


def downgrade() -> None:
    """Drops the web app's tables and columns. The chunk store is left as it is:
    it is reproducible from Zotero, but rebuilding it costs embedding calls."""
    op.execute(
        """
        DROP TABLE IF EXISTS messages, conversations, browsers, index_runs, collections, library;
        ALTER TABLE items DROP COLUMN IF EXISTS version, DROP COLUMN IF EXISTS date,
            DROP COLUMN IF EXISTS updated_at;
        ALTER TABLE attachments DROP COLUMN IF EXISTS content_type, DROP COLUMN IF EXISTS status,
            DROP COLUMN IF EXISTS error, DROP COLUMN IF EXISTS indexed_at;
        """
    )
