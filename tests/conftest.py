"""Shared fixtures. Nothing here reads the developer's `.env`."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest

from zotero_rag_web.settings import Settings

#: An existing server to create test databases on, instead of starting a container.
PG_URL_ENV = "ZRW_TEST_PG_URL"
#: Set in CI: a missing database fails the run instead of skipping its tests.
PG_REQUIRED_ENV = "ZRW_REQUIRE_PG"


def make_settings(**overrides: Any) -> Settings:
    """Settings from the arguments alone: no `.env`, no inherited environment."""
    return Settings(_env_file=None, **overrides)


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings read the environment; a developer's exported variables must not leak in."""
    settings_names = {field.upper() for field in Settings.model_fields}
    for name in list(os.environ):
        if name.upper() in settings_names:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(os.path.dirname(__file__))  # away from the project's .env


@pytest.fixture(scope="session")
def pg_server() -> Iterator[str]:
    """A PostgreSQL server with pgvector: `ZRW_TEST_PG_URL`, or a throwaway container."""
    configured = os.environ.get(PG_URL_ENV)
    if configured:
        yield configured
        return
    try:
        from testcontainers.community.postgres import PostgresContainer

        container = PostgresContainer("pgvector/pgvector:pg17", driver=None)
        container.start()
    except Exception as exc:  # noqa: BLE001 - no Docker, no image, no permission
        reason = f"no PostgreSQL for tests (set {PG_URL_ENV} or start Docker): {exc}"
        if os.environ.get(PG_REQUIRED_ENV):
            pytest.fail(reason)
        pytest.skip(reason)
    try:
        yield container.get_connection_url()
    finally:
        container.stop()


@pytest.fixture
def database_url(pg_server: str) -> Iterator[str]:
    """A new, empty database for one test, dropped afterwards."""
    name = f"t_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(pg_server, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    base, _, _ = pg_server.rpartition("/")
    try:
        yield f"{base}/{name}"
    finally:
        with psycopg.connect(pg_server, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
