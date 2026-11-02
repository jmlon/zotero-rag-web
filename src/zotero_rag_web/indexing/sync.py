"""One sync of the Zotero group into the database (PRD §6.5).

1. Take the sync lock; if another sync holds it, do nothing.
2. Read the library version. Unchanged since the last good sync: done.
3. Otherwise list the whole library. Choosing which attachment to index (a
   transcript over its PDF, a PDF over a snapshot) needs all of an item's
   attachments, and Zotero never re-sends unchanged children of an edited item
   (S1), so `since=` alone cannot plan correctly. ~30 requests for 1,000 items.
4. Rewrite the collection tree; upsert every item. A moved or re-tagged item is
   one row, and its chunks follow, since they join to it: nothing is re-embedded.
5. Plan with zotero-rag-core and index what is new or changed (by version).
6. Remove what is indexed but no longer in the library (trashed, deleted, or
   replaced by a preferred copy), unless that is a suspiciously large share.
7. Refresh BM25's statistics, record the fingerprint, move the cursor.
"""

from __future__ import annotations

import logging
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, cast

import httpx
import psycopg
from pyzotero import zotero

from zotero_rag_core.download import AttachmentFetcher, ensure_cached
from zotero_rag_core.embedding import build_embeddings
from zotero_rag_core.fingerprint import StoreFingerprint, advisory, blocking
from zotero_rag_core.indexer import (
    drop_replaced_copies,
    find_orphans,
    index_plan,
    prune_orphans,
    refresh_collection_keys,
)
from zotero_rag_core.planning import build_plan, library_membership
from zotero_rag_core.stores.pg import PgStore
from zotero_rag_core.zotero_api import (
    FileDownloader,
    build_client,
    build_collection_tree,
    fetch_collections,
    fetch_library_items,
    library_version,
)
from zotero_rag_web.db import libpq_url
from zotero_rag_web.indexing import library
from zotero_rag_web.indexing.lock import sync_lock
from zotero_rag_web.settings import Settings

logger = logging.getLogger(__name__)

Status = Literal["ok", "unchanged", "locked", "needs_attention", "error"]
Embedder = Callable[[list[str]], list[list[float]]]


class ZoteroClient(Protocol):
    """What the sync asks of pyzotero; a fake in tests."""

    def last_modified_version(self) -> int: ...
    def items(self, **kwargs: Any) -> Any: ...
    def collections(self, **kwargs: Any) -> Any: ...
    def everything(self, query: Any) -> Any: ...


class SyncError(RuntimeError):
    """The sync cannot run as configured; the message says what to do."""


@dataclass
class SyncReport:
    status: Status
    run_id: int | None = None
    version_before: int | None = None
    version_after: int | None = None
    items: int = 0
    collections: int = 0
    planned: int = 0
    indexed: int = 0
    reindexed: int = 0
    already_indexed: int = 0
    failed: int = 0
    no_text: int = 0
    chunks_added: int = 0
    embedding_tokens: int = 0
    removed_attachments: int = 0
    removed_chunks: int = 0
    replaced_copies: int = 0
    moved: int = 0
    removal_blocked: int = 0
    elapsed_s: float = 0.0
    message: str = ""
    failures: list[dict[str, Any]] = field(default_factory=list)

    def stats(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if k not in ("failures", "run_id")}


@dataclass
class Seams:
    """The network, injectable; `None` builds the real thing from the settings."""

    zot: ZoteroClient | None = None
    files: AttachmentFetcher | None = None
    embed: Embedder | None = None
    group_name: Callable[[], str | None] | None = None


def run_sync(
    settings: Settings,
    *,
    trigger: str = "manual",
    full: bool = False,
    allow_mass_removal: bool = False,
    seams: Seams | None = None,
) -> SyncReport:
    """Sync once. Never raises for a Zotero or indexing problem: it is recorded in
    `index_runs` and returned, so a scheduled job always leaves a trace."""
    settings.require("database", "zotero", "openrouter")
    seams = seams or Seams()
    url = libpq_url(settings.database_url)
    with sync_lock(url) as acquired:
        if not acquired:
            logger.info("another sync holds the lock; nothing to do")
            return SyncReport(status="locked", message="Another sync is running.")
        with psycopg.connect(url, autocommit=True) as conn:
            run_id = library.start_run(conn, trigger)
            report = SyncReport(status="ok", run_id=run_id)
            start = time.perf_counter()
            store = PgStore.from_url(url)
            try:
                _sync(settings, conn, store, report, full, allow_mass_removal, seams)
            except Exception as exc:  # noqa: BLE001 - recorded, and the cursor stays
                logger.exception("sync failed")
                report.status = "error"
                report.message = f"{type(exc).__name__}: {exc}"
            finally:
                store.close()
                report.elapsed_s = round(time.perf_counter() - start, 1)
                library.finish_run(conn, run_id, report.status, report.stats(), report.failures)
                if report.status in ("error", "needs_attention"):
                    group_id = settings.zotero_group_id or 0
                    library.record_library(
                        conn, group_id=group_id, name=None, status=report.status, version=None
                    )
    return report


def _sync(
    settings: Settings,
    conn: psycopg.Connection[Any],
    store: PgStore,
    report: SyncReport,
    full: bool,
    allow_mass_removal: bool,
    seams: Seams,
) -> None:
    config = settings.core_config
    group_id = int(settings.zotero_group_id or 0)
    # The core's fetchers are typed for pyzotero's client; a fake has the same methods.
    zot = cast(zotero.Zotero, seams.zot or build_client(config))

    # 2. Anything new? The version is read before listing, so a change made while
    # listing is seen again next time rather than skipped.
    report.version_before = library.last_version(conn)
    current = library_version(zot)
    report.version_after = current
    if not full and report.version_before == current:
        report.status = "unchanged"
        library.record_library(
            conn, group_id=group_id, name=None, status="unchanged", version=current
        )
        return

    _check_fingerprint(store, settings)

    # 3–4. The whole library, the tree and the items.
    roots = build_collection_tree(fetch_collections(zot))
    entries = fetch_library_items(zot)
    parents = [entry for entry in entries if library.is_parent(entry)]
    report.collections = library.replace_collections(conn, roots)
    report.items = library.upsert_items(conn, (library.item_row(e, group_id) for e in parents))

    # 5. Plan and index. Citations link by group id (S3), not the API's slug link.
    name = seams.group_name() if seams.group_name else _group_name(settings)
    plan = build_plan(
        [(name or "library", entries)],
        preference=list(settings.attachment_preference),
        index_notes=settings.index_notes,
        membership=library_membership(entries),
    )
    for attachment in plan.attachments:
        attachment.zotero_url = library.item_url(group_id, attachment.item_key)
    report.planned = len(plan.attachments)

    # 6a. Decide removals before writing anything, so a bad listing cannot both add
    # and remove. An empty or truncated listing would otherwise empty the index.
    indexed = store.attachment_keys()
    leaving = find_orphans(store, plan)
    share = len(leaving) / len(indexed) if indexed else 0.0
    removal_allowed = allow_mass_removal or share <= settings.prune_max_fraction

    # Credentials and width, checked once before any download: a rejected key would
    # otherwise surface as a failure of every attachment, in a run that "succeeds".
    embed: Embedder = seams.embed or build_embeddings(config).embed_documents
    _preflight(embed, settings)

    stats = _index(settings, store, plan, seams, embed)
    report.indexed, report.reindexed = stats.indexed, stats.reindexed
    report.already_indexed, report.failed, report.no_text = (
        stats.already_indexed,
        stats.failed,
        stats.no_text,
    )
    report.chunks_added, report.embedding_tokens = stats.chunks, stats.embedding_tokens
    report.failures = [failure.model_dump() for failure in stats.failures]

    report.replaced_copies, _ = drop_replaced_copies(store, plan)
    report.moved = refresh_collection_keys(store, plan)

    # 6b. Removals.
    if removal_allowed:
        report.removed_attachments, report.removed_chunks = prune_orphans(store, plan)
        library.delete_items_except(conn, {e["key"] for e in parents})
    else:
        report.removal_blocked = len(leaving)
        report.status = "needs_attention"
        report.message = (
            f"{len(leaving)} of {len(indexed)} indexed attachments ({share:.0%}) are no "
            f"longer in the library, more than PRUNE_MAX_FRACTION "
            f"({settings.prune_max_fraction:.0%}). Nothing was removed. If that is "
            "right, run `zrw index --allow-mass-removal`."
        )

    if report.failed and report.status == "ok":
        report.message = (
            f"{report.failed} attachment(s) could not be indexed; they are retried on the next run."
        )

    # 7. Statistics, fingerprint, cursor. The cursor stays while anything failed, so
    # the next run re-plans instead of finding the library "unchanged": already
    # indexed attachments are skipped by version, the failed ones are tried again.
    store.refresh_keyword_stats()
    store.write_fingerprint(
        StoreFingerprint.intended(config, embedding_dim=store.derived_dimension() or 0)
    )
    library.record_library(
        conn,
        group_id=group_id,
        name=name,
        status=report.status,
        version=current if report.status == "ok" and not report.failed else None,
    )


def _check_fingerprint(store: PgStore, settings: Settings) -> None:
    """Refuse to mix two embedding models' vectors in one store; warn about the rest.

    The core's rule: another model or width would search nonsense, so it stops the
    run; another chunking only shapes new chunks differently from old ones.
    """
    recorded = store.read_fingerprint()
    if recorded is None or store.count() == 0:
        return
    intended = StoreFingerprint.intended(
        settings.core_config, embedding_dim=settings.embedding_width
    )
    mismatches = recorded.compare(intended)
    if fatal := blocking(mismatches):
        raise SyncError(
            "The index was built with other embeddings than the current settings: "
            + "; ".join(str(m) for m in fatal)
            + ". Run `zrw reindex --repair` to re-embed it with EMBEDDING_MODEL."
        )
    for mismatch in advisory(mismatches):
        logger.warning(
            "%s. New chunks follow the current settings; `zrw reindex --rechunk` makes "
            "every chunk follow them.",
            mismatch,
        )


#: Embedded once per sync to check the key and the model's width before any work.
PROBE_TEXT = "zotero-rag-web preflight"


def _preflight(embed: Embedder, settings: Settings) -> None:
    """Raise if the embedding provider refuses us, or returns another width than set."""
    (vector,) = embed([PROBE_TEXT])
    if len(vector) != settings.embedding_width:
        raise SyncError(
            f"EMBEDDING_MODEL {settings.embedding_model!r} returned {len(vector)} dimensions, "
            f"but the settings say {settings.embedding_width}. Correct EMBEDDING_DIMENSIONS."
        )


def _index(settings: Settings, store: PgStore, plan: Any, seams: Seams, embed: Embedder) -> Any:
    files: AttachmentFetcher = seams.files or FileDownloader.from_config(settings.core_config)
    formats = {a.attachment_key: a.document_format for a in plan.attachments}
    # Files are only needed while they are being read: no cache survives the run.
    with tempfile.TemporaryDirectory(prefix="zrw-") as tmp:
        cache = Path(tmp)
        return index_plan(
            plan,
            store=store,
            download=lambda key: ensure_cached(files, key, cache, formats[key]),
            embed=embed,
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
            embedding_model=settings.embedding_model,
            workers=settings.index_workers,
        )


def _group_name(settings: Settings) -> str | None:
    """The group's name, for the UI's header; not worth failing a sync over."""
    key = settings.zotero_api_key.get_secret_value() if settings.zotero_api_key else ""
    try:
        response = httpx.get(
            f"https://api.zotero.org/groups/{settings.zotero_group_id}",
            headers={"Zotero-API-Key": key, "Zotero-API-Version": "3"},
            timeout=15,
        )
        response.raise_for_status()
        return str(response.json()["data"]["name"])
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        logger.warning("could not read the group's name: %s", exc)
        return None
