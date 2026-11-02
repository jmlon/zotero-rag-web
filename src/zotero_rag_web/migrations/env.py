"""Alembic environment: the database comes from DATABASE_URL, like everything else."""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool

from zotero_rag_web.db import metadata, sqlalchemy_url
from zotero_rag_web.settings import Settings

config = context.config
url = config.get_main_option("sqlalchemy.url") or Settings().database_url
if not url:
    raise SystemExit("Set DATABASE_URL to the database to migrate.")

engine = create_engine(sqlalchemy_url(url), poolclass=pool.NullPool)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
