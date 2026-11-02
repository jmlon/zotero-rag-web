"""Live check for the chat API (M3): real models, real papers, a real conversation.

Creates two "[m3]" papers with PDFs in the test group from `.env`, syncs them into a
throwaway pgvector container, and talks to the app as the UI will: a question, a
follow-up that only makes sense with history, a question in Spanish, and a
question whose answer a collection filter excludes. Prints the transcript for
review (PLAN M3.5) and checks what can be checked mechanically. Everything it
creates in the group is deleted at the end. Cost: a few cents.

    uv run python scripts/live_chat_check.py
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any

from alembic import command
from fastapi.testclient import TestClient
from pydantic import SecretStr
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from pyzotero import zotero
from testcontainers.community.postgres import PostgresContainer

from zotero_rag_core.querying import NO_CONTEXT_ANSWER
from zotero_rag_web.app import create_app
from zotero_rag_web.cli import alembic_config
from zotero_rag_web.indexing.sync import run_sync
from zotero_rag_web.settings import Settings

ROOT = Path(__file__).parent.parent
results: list[tuple[str, bool]] = []

FOG = [
    "A fog node sits between the edge devices and the cloud.",
    "Fog nodes run compute close to where data is produced, which reduces latency.",
]
ECMP = [
    "ECMP routing spreads flows over equal-cost paths by hashing packet headers.",
    "Hash collisions can put several elephant flows on one path and congest it.",
]


def check(name: str, ok: bool, detail: Any = "") -> None:
    results.append((name, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail != "" else ""))


def pdf(path: Path, pages: list[str]) -> Path:
    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=500, height=200)
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 9 Tf 10 100 Td ({text}) Tj ET".encode())
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
    return str(next(iter((response.get("successful") or {}).values()))["key"])


def ask(client: TestClient, conversation: str, question: str) -> dict[str, Any]:
    """Ask, and return the stages, the standalone question and the `done` payload."""
    response = client.post(
        f"/api/conversations/{conversation}/messages", json={"question": question}
    )
    assert response.status_code == 200, response.text
    stages, standalone, done = [], None, {}
    for block in response.text.split("\n\n"):
        lines = dict(
            line.split(": ", 1)
            for line in block.splitlines()
            if ": " in line and not line.startswith(":")
        )
        if "event" not in lines:
            continue
        data = json.loads(lines["data"])
        if lines["event"] == "status":
            stages.append(data["stage"])
        elif lines["event"] == "query":
            standalone = data["standalone"]
        elif lines["event"] in ("done", "error"):
            done = {"event": lines["event"], **data}
    print(f"\n  Q: {question}")
    if standalone:
        print(f"  searched for: {standalone}")
    print(f"  A: {done.get('answer') or done}")
    for source in done.get("sources", []):
        print(f"     [{source['number']}] {source['title']}, {source['locator']}")
    if done.get("usage"):
        print(f"     {done['usage']}")
    return {"stages": stages, "standalone": standalone, **done}


def main() -> int:
    base = Settings(_env_file=ROOT / ".env")  # type: ignore[call-arg]
    base.require("zotero", "openrouter")
    key = base.zotero_api_key.get_secret_value() if base.zotero_api_key else ""
    z = zotero.Zotero(str(base.zotero_group_id), "group", key)
    made: list[str] = []
    collections: list[str] = []
    container = PostgresContainer("pgvector/pgvector:pg17", driver=None)
    container.start()
    try:
        url = container.get_connection_url()
        command.upgrade(alembic_config(url), "head")
        # model_copy does not validate, so the secret must already be a SecretStr.
        settings = base.model_copy(
            update={
                "database_url": url,
                "log_format": "console",
                "secret_key": SecretStr("live-check"),
                "cookie_secure": False,
            }
        )

        routing = created(z.create_collections([{"name": "[m3] Routing"}]))
        collections.append(routing)
        for title, pages, folder in (
            ("[m3] What is a Fog Node", FOG, None),
            ("[m3] ECMP and elephant flows", ECMP, routing),
        ):
            item = z.item_template("journalArticle")
            item.update(title=title, date="2020", collections=[folder] if folder else [])
            paper = created(z.create_items([item]))
            made.append(paper)
            upload = z.attachment_simple(
                [str(pdf(Path(f"/tmp/{paper}.pdf"), pages))], parentid=paper
            )
            made.append(str((upload.get("success") or upload.get("unchanged"))[0]["key"]))
        sync = run_sync(settings, trigger="live-check")
        check(
            "sync indexed both papers",
            sync.status == "ok" and sync.indexed == 2,
            sync.message or f"indexed={sync.indexed}",
        )

        with TestClient(create_app(settings)) as client:
            library = client.get("/api/library").json()
            check("library reports the index", library["indexed"], library)
            chat = client.post("/api/conversations", json={}).json()["id"]

            first = ask(client, chat, "What is ECMP and how does it choose a path?")
            check(
                "answered from the ECMP paper",
                [s["title"] for s in first.get("sources", [])][:1]
                == ["[m3] ECMP and elephant flows"],
            )
            check("no condensing on the first question", "condensing" not in first["stages"])

            follow = ask(client, chat, "What problem can that cause for elephant flows?")
            check(
                "the follow-up was condensed",
                follow["standalone"] is not None and "ECMP" in follow["standalone"],
                follow["standalone"],
            )
            check("the follow-up is answered with a citation", bool(follow.get("sources")))

            spanish = ask(client, chat, "¿Dónde se ubica un nodo fog?")
            check(
                "a Spanish question cites the fog paper",
                any(s["title"] == "[m3] What is a Fog Node" for s in spanish.get("sources", [])),
            )

            filtered = client.post(
                "/api/conversations", json={"filters": {"collection": routing}}
            ).json()["id"]
            outside = ask(client, filtered, "Where does a fog node sit?")
            check(
                "a filter excluding the answer yields no fog source",
                all(s["title"] != "[m3] What is a Fog Node" for s in outside.get("sources", [])),
                outside.get("answer") == NO_CONTEXT_ANSWER,
            )

            costs = [r.get("usage", {}).get("total_cost", 0) for r in (first, follow, spanish)]
            sources = {r.get("usage", {}).get("cost_source") for r in (first, follow, spanish)}
            # A streamed answer has no provider cost; it must be priced, not counted as free.
            check(
                "streamed answers are priced at list price",
                sources == {"list_price"} and all(c > 1e-5 for c in costs),
                (costs, sources),
            )
    finally:
        for item_key in reversed(made):
            try:
                z.delete_item(z.item(item_key))
            except Exception as exc:  # noqa: BLE001 - deleting a parent removes its PDF
                if "404" not in str(exc):
                    print(f"cleanup: {item_key}: {exc}")
        for collection_key in collections:
            try:
                z.delete_collection(z.collection(collection_key))
            except Exception as exc:  # noqa: BLE001
                print(f"cleanup: collection {collection_key}: {exc}")
        container.stop()

    failed = [name for name, ok in results if not ok]
    print(
        f"\n{len(results) - len(failed)}/{len(results)} passed"
        + (f"; failed: {failed}" if failed else "")
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
