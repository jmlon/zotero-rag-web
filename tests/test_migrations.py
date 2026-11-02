"""`zrw migrate`: the schema it creates, run against a real PostgreSQL with pgvector."""

from __future__ import annotations

import psycopg
import pytest
from typer.testing import CliRunner

from zotero_rag_core.stores.pg import PgFullTextSearcher, PgStore
from zotero_rag_web.cli import app
from zotero_rag_web.db import CORE_TABLE_ADDITIONS, CORE_TABLES, metadata

runner = CliRunner()


def migrate(database_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    result = runner.invoke(app, ["migrate"])
    assert result.exit_code == 0, result.output
    assert "up to date" in result.output


def columns(database_url: str) -> dict[str, set[str]]:
    with psycopg.connect(database_url) as conn:
        rows = conn.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'public'"
        ).fetchall()
    found: dict[str, set[str]] = {}
    for table, column in rows:
        found.setdefault(table, set()).add(column)
    return found


def test_migrate_creates_the_core_and_web_tables(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrate(database_url, monkeypatch)
    found = columns(database_url)
    assert set(CORE_TABLES) | set(metadata.tables) <= set(found)


def test_the_web_tables_match_the_code_that_queries_them(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The migration is written by hand; this catches it drifting from `db.metadata`."""
    migrate(database_url, monkeypatch)
    found = columns(database_url)
    for name, table in metadata.tables.items():
        assert found[name] == {column.name for column in table.columns}, name
    for name, added in CORE_TABLE_ADDITIONS.items():
        assert {column.name for column in added} <= found[name], name


def test_migrate_is_safe_to_run_on_every_deploy(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrate(database_url, monkeypatch)
    migrate(database_url, monkeypatch)
    with psycopg.connect(database_url) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == ("0001",)


def test_the_core_store_works_on_the_migrated_database(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What M2's indexer will write, and M3's chat will read, works on this schema."""
    migrate(database_url, monkeypatch)
    store = PgStore.from_url(database_url)
    try:
        store.add(
            ids=["A:1:0", "B:1:0"],
            embeddings=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
            documents=["Fog nodes sit between devices and the cloud.", "ECMP hashing."],
            metadatas=[
                {"attachment_key": "A", "item_key": "IA", "title": "Fog", "year_int": 2016},
                {"attachment_key": "B", "item_key": "IB", "title": "ECMP", "year_int": 2020},
            ],
        )
        assert [c.id for c in store.query([1.0, 0.0, 0.0], 1)] == ["A:1:0"]
        assert [c.id for c in PgFullTextSearcher(store).search("ECMP", 5)] == ["B:1:0"]
    finally:
        store.close()


def test_migrate_without_a_database_says_what_to_set() -> None:
    result = runner.invoke(app, ["migrate"])
    assert result.exit_code == 2
    assert "DATABASE_URL" in result.output


def test_config_shows_settings_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://zrw:hunter2@db/zrw")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret")
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0
    assert "hunter2" not in result.output and "sk-or-secret" not in result.output
    assert '"openrouter_api_key": "set"' in result.output


def test_invalid_settings_fail_with_the_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBEDDING_MODEL", "qwen/qwen3-embedding-8b")
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 2
    assert "at most 2000" in result.output
