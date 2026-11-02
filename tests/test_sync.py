"""`zrw index`: syncing a Zotero group into the database (PRD §6.5, §13 item 2).

Zotero, its file store and the embedding model are fakes; the database, the
migration, zotero-rag-core's planner, indexer and PgStore are all real.
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from typer.testing import CliRunner

from tests.conftest import make_settings
from zotero_rag_core.store import Filters
from zotero_rag_core.stores.pg import PgFullTextSearcher, PgStore
from zotero_rag_web.cli import app
from zotero_rag_web.indexing.lock import sync_lock
from zotero_rag_web.indexing.sync import PROBE_TEXT, Seams, SyncReport, run_sync
from zotero_rag_web.settings import Settings

GROUP = 6743389
WIDTH = 8


# --- a fake group library ------------------------------------------------------------


def pdf_bytes(*pages: str) -> bytes:
    """A real PDF with one line of text per page."""
    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=300, height=200)
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 10 Tf 10 100 Td ({text}) Tj ET".encode())
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Contents")] = writer._add_object(stream)  # noqa: SLF001
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}  # noqa: SLF001
        )
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


class Library:
    """A group library as the Web API shows it, editable between syncs."""

    def __init__(self) -> None:
        self.version = 10
        self.entries: dict[str, dict[str, Any]] = {}
        self.files: dict[str, bytes] = {}
        self.collection_list: list[dict[str, Any]] = []
        self.fail: Exception | None = None

    def bump(self) -> int:
        self.version += 1
        return self.version

    def collection(self, key: str, name: str, parent: str | None = None) -> None:
        self.collection_list.append(
            {
                "key": key,
                "data": {"key": key, "name": name, "parentCollection": parent or False},
                "meta": {"numItems": 0},
            }
        )

    def paper(
        self,
        key: str,
        title: str,
        *,
        collections: list[str] | None = None,
        tags: list[str] | None = None,
        date: str = "2021",
    ) -> None:
        self.entries[key] = {
            "key": key,
            "version": self.bump(),
            "data": {
                "key": key,
                "version": self.version,
                "itemType": "journalArticle",
                "title": title,
                "date": date,
                "collections": collections or [],
                "tags": [{"tag": tag} for tag in tags or []],
                "creators": [{"creatorType": "author", "lastName": "Doe", "firstName": "Jane"}],
            },
        }

    def pdf(self, key: str, parent: str, *pages: str) -> None:
        self.files[key] = pdf_bytes(*pages)
        self.entries[key] = {
            "key": key,
            "version": self.bump(),
            "data": {
                "key": key,
                "version": self.version,
                "itemType": "attachment",
                "parentItem": parent,
                "contentType": "application/pdf",
                "linkMode": "imported_file",
                "filename": f"{key}.pdf",
                "md5": f"md5-{self.version}",
            },
        }

    def note(self, key: str, parent: str, html: str) -> None:
        self.entries[key] = {
            "key": key,
            "version": self.bump(),
            "data": {
                "key": key,
                "version": self.version,
                "itemType": "note",
                "parentItem": parent,
                "note": html,
            },
        }

    def edit(self, key: str, **fields: Any) -> None:
        entry = self.entries[key]
        entry["data"].update(fields)
        entry["version"] = entry["data"]["version"] = self.bump()

    def remove(self, key: str) -> None:
        self.entries.pop(key)
        self.bump()

    # --- what pyzotero offers --------------------------------------------------
    def last_modified_version(self) -> int:
        if self.fail:
            raise self.fail
        return self.version

    def items(self, **kwargs: Any) -> str:
        return "items"

    def collections(self, **kwargs: Any) -> str:
        return "collections"

    def everything(self, query: str) -> list[dict[str, Any]]:
        if query == "collections":
            return list(self.collection_list)
        return [dict(entry) for entry in self.entries.values()]

    def file(self, key: str) -> bytes:
        return self.files[key]


class CountingEmbedder:
    """Deterministic vectors; counts the texts it is asked to embed."""

    def __init__(self) -> None:
        self.texts: list[str] = []
        self.probes = 0

    def __call__(self, texts: list[str]) -> list[list[float]]:
        if texts == [PROBE_TEXT]:
            self.probes += 1  # the sync's credential check, not indexing
        else:
            self.texts.extend(texts)
        return [
            [float(len(t) % 7 + 1)] + [float(i == n % WIDTH) for i in range(1, WIDTH)]
            for n, t in enumerate(texts)
        ]


@pytest.fixture
def migrated(database_url: str, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("DATABASE_URL", database_url)
    assert CliRunner().invoke(app, ["migrate"]).exit_code == 0
    monkeypatch.delenv("DATABASE_URL")
    return database_url


def settings_for(database_url: str, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "database_url": database_url,
        "zotero_group_id": GROUP,
        "zotero_api_key": "z",
        "openrouter_api_key": "o",
        "embedding_model": "test/embedder",
        "embedding_dimensions": WIDTH,
        "index_workers": 1,
    }
    return make_settings(**(values | overrides))


@pytest.fixture
def lib() -> Library:
    """Methods > Graphs; paper P1 (filed, tagged, PDF + note), P2 (unfiled, PDF)."""
    library = Library()
    library.collection("METH", "Methods")
    library.collection("GRAPH", "Graphs", "METH")
    library.paper("P1", "Retrieval with graphs", collections=["GRAPH"], tags=["RAG"])
    library.pdf("A1", "P1", "Graph retrieval augments generation.", "Results on ECMP.")
    library.note("N1", "P1", "<p>Reader note: compare with GIVE.</p>")
    library.paper("P2", "Fog computing", date="2016")
    library.pdf("A2", "P2", "A fog node sits between the edge and the cloud.")
    return library


class Harness:
    def __init__(self, url: str, library: Library) -> None:
        self.url, self.library, self.embed = url, library, CountingEmbedder()

    def sync(self, **kwargs: Any) -> SyncReport:
        settings = kwargs.pop("settings", None) or settings_for(self.url)
        seams = Seams(
            zot=self.library,
            files=self.library,
            embed=self.embed,
            group_name=lambda: "AI_EDU_UPB",
        )
        return run_sync(settings, seams=seams, **kwargs)

    def rows(self, sql: str, *params: Any) -> list[tuple[Any, ...]]:
        with psycopg.connect(self.url) as conn:
            return conn.execute(sql, params).fetchall()

    def store(self) -> Iterator[PgStore]:
        store = PgStore.from_url(self.url)
        try:
            yield store
        finally:
            store.close()


@pytest.fixture
def h(migrated: str, lib: Library) -> Harness:
    return Harness(migrated, lib)


def chunk_attachments(h: Harness) -> set[str]:
    return {row[0] for row in h.rows("SELECT DISTINCT attachment_key FROM chunks")}


# --- the first sync ------------------------------------------------------------------


def test_the_first_sync_indexes_the_whole_library(h: Harness) -> None:
    report = h.sync()
    assert report.status == "ok", report.message
    assert (report.items, report.collections, report.planned) == (2, 2, 3)
    assert chunk_attachments(h) == {"A1", "A2", "N1"}
    assert h.rows("SELECT path FROM collections ORDER BY path") == [
        ("Methods",),
        ("Methods/Graphs",),
    ]
    assert h.rows("SELECT name, last_version, last_sync_status FROM library") == [
        ("AI_EDU_UPB", h.library.version, "ok")
    ]
    (run,) = h.rows("SELECT status, stats->>'indexed' FROM index_runs")
    assert run == ("ok", "3")


def test_citations_link_to_the_item_by_group_id(h: Harness) -> None:
    h.sync()
    for store in h.store():
        (chunk, *_) = store.get_by_attachment("A1")
    assert chunk.metadata["zotero_url"] == f"https://www.zotero.org/groups/{GROUP}/items/P1"


def test_filters_and_keyword_search_work_after_a_sync(h: Harness) -> None:
    h.sync()
    for store in h.store():
        assert not store.keyword_stats_stale()
        hits = PgFullTextSearcher(store).search("fog cloud", 5)
        assert {c.metadata["attachment_key"] for c in hits} == {"A2"}
        graphs = store.query([1.0] * WIDTH, 10, Filters(collection_keys=frozenset({"GRAPH"})))
        assert {c.metadata["attachment_key"] for c in graphs} == {"A1", "N1"}
        old = store.query([1.0] * WIDTH, 10, Filters(year_max=2018, include_undated=False))
        assert {c.metadata["attachment_key"] for c in old} == {"A2"}


# --- later syncs: only what changed ---------------------------------------------------


def test_an_unchanged_library_costs_one_request_and_no_embedding(h: Harness) -> None:
    h.sync()
    before = len(h.embed.texts)
    report = h.sync()
    assert report.status == "unchanged"
    assert len(h.embed.texts) == before


def test_a_new_item_is_the_only_thing_embedded(h: Harness) -> None:
    h.sync()
    h.embed.texts.clear()
    h.library.paper("P3", "New paper")
    h.library.pdf("A3", "P3", "Brand new transformer results.")
    report = h.sync()
    assert (report.status, report.indexed, report.already_indexed) == ("ok", 1, 3)
    assert h.embed.texts == ["Brand new transformer results."]


def test_moving_and_retagging_an_item_embeds_nothing(h: Harness) -> None:
    h.sync()
    h.embed.texts.clear()
    h.library.edit("P2", collections=["METH"], tags=[{"tag": "edge"}])
    report = h.sync()
    assert report.status == "ok"
    assert h.embed.texts == []
    for store in h.store():
        moved = store.query([1.0] * WIDTH, 10, Filters(collection_keys=frozenset({"METH"})))
        tagged = store.query([1.0] * WIDTH, 10, Filters(tags=frozenset({"edge"})))
    assert {c.metadata["attachment_key"] for c in moved} == {"A2"}
    assert {c.metadata["attachment_key"] for c in tagged} == {"A2"}


def test_a_replaced_pdf_is_re_embedded_and_its_old_text_is_gone(h: Harness) -> None:
    h.sync()
    h.embed.texts.clear()
    h.library.files["A2"] = pdf_bytes("A rewritten paper about edge caching.")
    h.library.edit("A2", md5="new")
    report = h.sync()
    assert (report.status, report.reindexed) == ("ok", 1)
    assert h.embed.texts == ["A rewritten paper about edge caching."]
    texts = {row[0] for row in h.rows("SELECT text FROM chunks WHERE attachment_key = 'A2'")}
    assert texts == {"A rewritten paper about edge caching."}


def test_a_deleted_or_trashed_item_leaves_the_index(h: Harness) -> None:
    """Trashed and deleted look the same to a full listing: no longer there."""
    h.library.paper("P3", "Third")
    h.library.pdf("A3", "P3", "Third paper text.")
    h.sync()
    for key in ("P2", "A2"):
        h.library.remove(key)
    # One of four attachments is 25%: above the default 20%, made for real libraries.
    report = h.sync(settings=settings_for(h.url, prune_max_fraction=0.5))
    assert (report.status, report.removed_attachments) == ("ok", 1)
    assert chunk_attachments(h) == {"A1", "N1", "A3"}
    assert h.rows("SELECT key FROM items ORDER BY key") == [("P1",), ("P3",)]


# --- what must not happen ------------------------------------------------------------


def test_an_empty_listing_does_not_empty_the_index(h: Harness) -> None:
    """A glitch returning no items must not delete everything (PRUNE_MAX_FRACTION)."""
    h.sync()
    version = h.library.version
    h.library.entries.clear()
    h.library.bump()
    report = h.sync()
    assert report.status == "needs_attention"
    assert report.removal_blocked == 3
    assert chunk_attachments(h) == {"A1", "A2", "N1"}
    assert h.rows("SELECT last_version, last_sync_status FROM library") == [
        (version, "needs_attention")
    ]

    confirmed = h.sync(allow_mass_removal=True)
    assert confirmed.status == "ok"
    assert chunk_attachments(h) == set()


def test_a_second_sync_while_one_runs_does_nothing(h: Harness) -> None:
    with sync_lock(h.url) as held:
        assert held
        report = h.sync()
    assert report.status == "locked"
    assert h.rows("SELECT count(*) FROM index_runs") == [(0,)]


def test_another_embedding_model_is_refused_until_repaired(h: Harness) -> None:
    """Vectors of two models in one store would search nonsense."""
    h.sync()
    h.library.bump()
    other = settings_for(h.url, embedding_model="test/other-embedder")
    report = h.sync(settings=other)
    assert report.status == "error"
    assert "reindex --repair" in report.message
    assert h.rows("SELECT status FROM index_runs ORDER BY id DESC LIMIT 1") == [("error",)]


def test_another_chunking_only_warns(h: Harness, caplog: pytest.LogCaptureFixture) -> None:
    """The core's rule: new chunks take the new shape; `reindex --rechunk` redoes all."""
    h.sync()
    h.library.bump()
    report = h.sync(settings=settings_for(h.url, chunk_size=500, chunk_overlap=100))
    assert report.status == "ok"
    assert "reindex --rechunk" in caplog.text


def test_a_zotero_failure_is_recorded_and_the_cursor_stays(h: Harness) -> None:
    h.sync()
    version = h.library.version
    h.library.bump()
    h.library.fail = ConnectionError("api.zotero.org unreachable")
    report = h.sync()
    assert report.status == "error"
    assert "unreachable" in report.message
    assert h.rows("SELECT last_version, last_sync_status FROM library") == [(version, "error")]


def test_status_reports_counts_and_runs(h: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    h.sync()
    monkeypatch.setenv("DATABASE_URL", h.url)
    result = CliRunner().invoke(app, ["status"])
    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)
    assert (summary["items"], summary["attachments"], summary["collections"]) == (2, 3, 2)
    assert summary["library"]["status"] == "ok"
    assert [run["status"] for run in summary["recent_runs"]] == ["ok"]
    assert summary["fingerprint"]["zotero_rag.embedding_model"] == "test/embedder"


def test_reindex_needs_exactly_one_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    result = CliRunner().invoke(app, ["reindex"])
    assert result.exit_code == 2
    assert "exactly one" in result.output


# --- failures must not be forgotten ---------------------------------------------------


def test_a_rejected_key_fails_the_run_before_any_download(h: Harness) -> None:
    """Found live: a 401 per attachment used to end in an "ok" run with a moved cursor."""
    from zotero_rag_core.errors import RejectedCredentialError

    def rejected(texts: list[str]) -> list[list[float]]:
        raise RejectedCredentialError("OpenRouter", "OPENROUTER_API_KEY", "401 - User not found")

    h.embed = rejected  # type: ignore[assignment]
    report = h.sync()
    assert report.status == "error"
    assert "401" in report.message
    assert chunk_attachments(h) == set()
    assert h.rows("SELECT last_version FROM library") == [(None,)]


def test_a_model_of_another_width_than_configured_is_caught(h: Harness) -> None:
    def wider(texts: list[str]) -> list[list[float]]:
        return [[0.5] * (WIDTH + 4) for _ in texts]

    h.embed = wider  # type: ignore[assignment]
    report = h.sync()
    assert report.status == "error"
    assert "EMBEDDING_DIMENSIONS" in report.message


def test_a_failed_attachment_is_retried_on_the_next_run(h: Harness) -> None:
    """Without this, an unchanged library would never be looked at again."""
    good = h.library.files.pop("A2")
    first = h.sync()
    assert (first.status, first.failed) == ("ok", 1)
    assert "retried" in first.message
    assert h.rows("SELECT last_version FROM library") == [(None,)]

    h.library.files["A2"] = good
    h.embed.texts.clear()
    second = h.sync()  # nothing changed in Zotero, yet the failure is retried
    assert (second.status, second.indexed, second.failed) == ("ok", 1, 0)
    assert chunk_attachments(h) == {"A1", "A2", "N1"}
    assert h.rows("SELECT last_version FROM library") == [(h.library.version,)]
    assert h.sync().status == "unchanged"
