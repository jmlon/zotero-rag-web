"""API filters → zotero-rag-core `Filters`, with a collection expanded to its subtree."""

from __future__ import annotations

import psycopg
import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from zotero_rag_core.store import Filters
from zotero_rag_web.chat.filters import SearchFilters, UnknownCollectionError, to_core
from zotero_rag_web.cli import app


@pytest.fixture
def conn(database_url: str, monkeypatch: pytest.MonkeyPatch) -> psycopg.Connection[object]:
    """A migrated database holding NET > {ROUTING, TRANSPORT > QUIC} and OTHER."""
    monkeypatch.setenv("DATABASE_URL", database_url)
    assert CliRunner().invoke(app, ["migrate"]).exit_code == 0
    connection = psycopg.connect(database_url, autocommit=True)
    connection.execute(
        "INSERT INTO collections (key, parent_key, name, path) VALUES "
        "('NET', NULL, 'Networking', 'Networking'), "
        "('ROUTING', 'NET', 'Routing', 'Networking/Routing'), "
        "('TRANSPORT', 'NET', 'Transport', 'Networking/Transport'), "
        "('QUIC', 'TRANSPORT', 'QUIC', 'Networking/Transport/QUIC'), "
        "('OTHER', NULL, 'Other', 'Other')"
    )
    return connection


def test_no_filters_search_everything(conn: psycopg.Connection[object]) -> None:
    assert to_core(SearchFilters(), conn) is None
    assert to_core(SearchFilters(tags=[]), conn) is None


def test_a_collection_covers_its_whole_subtree(conn: psycopg.Connection[object]) -> None:
    core = to_core(SearchFilters(collection="NET"), conn)
    assert core == Filters(collection_keys=frozenset({"NET", "ROUTING", "TRANSPORT", "QUIC"}))
    leaf = to_core(SearchFilters(collection="QUIC"), conn)
    assert leaf is not None and leaf.collection_keys == frozenset({"QUIC"})


def test_an_unknown_collection_is_an_error_not_an_empty_search(
    conn: psycopg.Connection[object],
) -> None:
    with pytest.raises(UnknownCollectionError):
        to_core(SearchFilters(collection="GONE"), conn)


def test_years_and_tags_pass_through(conn: psycopg.Connection[object]) -> None:
    core = to_core(
        SearchFilters(year_min=2015, year_max=2020, include_undated=False, tags=["RAG", "KG"]),
        conn,
    )
    assert core == Filters(
        year_min=2015, year_max=2020, include_undated=False, tags=frozenset({"RAG", "KG"})
    )


def test_bad_input_is_refused() -> None:
    with pytest.raises(ValidationError):
        SearchFilters(year_min=2020, year_max=2010)
    with pytest.raises(ValidationError):
        SearchFilters.model_validate({"collection": "NET", "author": "Doe"})
