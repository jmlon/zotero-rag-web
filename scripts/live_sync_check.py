"""Live acceptance check for `zrw index` (M2): the real Zotero API and real embeddings.

The unit tests (tests/test_sync.py) fake Zotero and the embedder. This runs the
same sync against the test group in `.env` (ZOTERO_GROUP_ID, a ZOTERO_API_KEY with
write access, OPENROUTER_API_KEY), into a throwaway pgvector container:

1. create a paper with a PDF and a note, titled "[m2] ...", and sync;
2. search it, by keyword and by a real embedding of a question;
3. move it to a collection and re-tag it, and sync: nothing may be re-embedded;
4. delete it, and sync: the mass-removal guard must hold, then remove when allowed.

Everything it creates in the group is deleted at the end, also on failure.
Embedding cost: a few chunks of text-embedding-3-small.

    uv run python scripts/live_sync_check.py
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import Any

from alembic import command
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from pyzotero import zotero
from testcontainers.community.postgres import PostgresContainer

from zotero_rag_core.embedding import build_embeddings
from zotero_rag_core.store import Filters
from zotero_rag_core.stores.pg import PgFullTextSearcher, PgStore
from zotero_rag_web.cli import alembic_config
from zotero_rag_web.indexing.sync import run_sync
from zotero_rag_web.settings import Settings

ROOT = Path(__file__).parent.parent
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: Any = "") -> None:
    results.append((name, ok, str(detail)))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail != "" else ""))


def pdf(path: Path, *pages: str) -> Path:
    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=400, height=200)
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
    path.write_bytes(buffer.getvalue())
    return path


def created(response: dict[str, Any]) -> str:
    first = next(iter((response.get("successful") or {}).values()))
    return str(first["key"])


def main() -> int:
    base = Settings(_env_file=ROOT / ".env")  # type: ignore[call-arg]
    base.require("zotero", "openrouter")
    key = base.zotero_api_key.get_secret_value() if base.zotero_api_key else ""
    z = zotero.Zotero(str(base.zotero_group_id), "group", key)
    made_items: list[str] = []
    made_collections: list[str] = []

    container = PostgresContainer("pgvector/pgvector:pg17", driver=None)
    container.start()
    try:
        url = container.get_connection_url()
        command.upgrade(alembic_config(url), "head")
        settings = base.model_copy(update={"database_url": url, "log_format": "console"})

        # 1. A paper, a PDF and a note.
        collection = created(z.create_collections([{"name": "[m2] Methods"}]))
        made_collections.append(collection)
        paper = z.item_template("journalArticle")
        paper.update(
            title="[m2] Fog computing at the network edge",
            date="2016",
            tags=[{"tag": "m2-check"}],
            creators=[{"creatorType": "author", "firstName": "Eva", "lastName": "Tordera"}],
        )
        paper_key = created(z.create_items([paper]))
        made_items.append(paper_key)
        note = z.item_template("note")
        note["note"] = "<p>[m2] Reader note: fog nodes reduce latency for IoT devices.</p>"
        made_items.append(created(z.create_items([note], parentid=paper_key)))
        file = pdf(
            Path("/tmp/zrw-m2-check.pdf"),
            "A fog node sits between the edge devices and the cloud.",
            "It provides compute closer to the data source, reducing latency.",
        )
        uploaded = z.attachment_simple([str(file)], parentid=paper_key)
        pdf_key = str((uploaded.get("success") or uploaded.get("unchanged"))[0]["key"])
        made_items.append(pdf_key)

        first = run_sync(settings, trigger="live-check")
        check("first sync ok", first.status == "ok", first.message or first.stats())
        check("PDF and note indexed", first.indexed >= 2, f"indexed={first.indexed}")

        store = PgStore.from_url(url)
        try:
            pdf_chunks = store.get_by_attachment(pdf_key)
            check(
                "PDF text extracted",
                any("fog node" in c.text for c in pdf_chunks),
                [c.text[:60] for c in pdf_chunks],
            )
            check(
                "citation links by group id",
                pdf_chunks
                and pdf_chunks[0].metadata.get("zotero_url")
                == f"https://www.zotero.org/groups/{base.zotero_group_id}/items/{paper_key}",
                pdf_chunks[0].metadata.get("zotero_url") if pdf_chunks else None,
            )
            hits = PgFullTextSearcher(store).search("fog latency", 3)
            check(
                "keyword search finds it",
                bool(hits) and hits[0].metadata["item_key"] == paper_key,
                [h.id for h in hits],
            )
            question = build_embeddings(settings.core_config).embed_query(
                "What sits between edge devices and the cloud?"
            )
            nearest = store.query(question, 1)
            check(
                "vector search finds it with a real embedding",
                bool(nearest) and nearest[0].metadata["item_key"] == paper_key,
                [(c.id, round(c.distance or 0, 3)) for c in nearest],
            )
            chunk_ids = {c.id for c in store.all_chunks()}

            # 3. Move and re-tag: metadata only.
            current = z.item(paper_key)["data"]
            current["collections"] = [collection]
            current["tags"] = [{"tag": "m2-check"}, {"tag": "edge"}]
            z.update_item(current)
            second = run_sync(settings, trigger="live-check")
            check(
                "move + re-tag synced without embedding",
                second.status == "ok" and second.indexed == 0 and second.reindexed == 0,
                second.stats(),
            )
            check("chunks untouched", {c.id for c in store.all_chunks()} == chunk_ids, "")
            moved = store.query(question, 5, Filters(collection_keys=frozenset({collection})))
            tagged = store.query(question, 5, Filters(tags=frozenset({"edge"})))
            check(
                "filters see the new folder and tag",
                bool(moved) and bool(tagged),
                (len(moved), len(tagged)),
            )

            # 4. Delete: everything indexed is leaving, so the guard must hold first.
            z.delete_item(z.item(paper_key))
            made_items.clear()
            held = run_sync(settings, trigger="live-check")
            check(
                "guard holds a removal of everything",
                held.status == "needs_attention" and store.count() > 0,
                held.message,
            )
            removed = run_sync(settings, trigger="live-check", allow_mass_removal=True)
            check(
                "removed once allowed",
                removed.status == "ok" and store.count() == 0,
                removed.stats(),
            )
        finally:
            store.close()
    finally:
        for item_key in reversed(made_items):
            try:
                z.delete_item(z.item(item_key))
            except Exception as exc:  # noqa: BLE001 - already gone is fine
                if "404" not in str(exc):
                    print(f"cleanup: {item_key}: {exc}")
        for collection_key in made_collections:
            try:
                z.delete_collection(z.collection(collection_key))
            except Exception as exc:  # noqa: BLE001
                print(f"cleanup: collection {collection_key}: {exc}")
        container.stop()

    failed = [name for name, ok, _ in results if not ok]
    print(
        f"\n{len(results) - len(failed)}/{len(results)} passed"
        + (f"; failed: {failed}" if failed else "")
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
