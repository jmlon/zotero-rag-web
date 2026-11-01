"""Spikes S1 and S3 (PLAN.md): the Zotero group API as zotero-rag-web's indexer uses it.

S1: does the group API give the incremental indexer everything it needs?
    whole-library fetch, `since=` deltas, trash and deletions, file downloads,
    and the library version.
S3: what do group items' web links look like, and do they open?

Runs against the group in `.env` (ZOTERO_GROUP_ID, ZOTERO_API_KEY with write
access). It creates a few items titled "[spike] ...", observes the API, and deletes
everything it created, also when a check fails. Items it did not create are only
read. Uses zotero-rag-core's own fetchers, so it tests the code the indexer will run.

    ../zotero-rag/.venv/bin/python spikes/s1_s3_group_api.py
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values
from pyzotero import zotero

from zotero_rag_core import zotero_api as api
from zotero_rag_core.metadata import web_url
from zotero_rag_core.planning import build_plan, library_membership

HERE = Path(__file__).parent
OUT = HERE / "out"
CLI_REPO = HERE.parent.parent / "zotero-rag"
sys.path.insert(0, str(CLI_REPO))
from tests.conftest import make_pdf_bytes  # noqa: E402 - the CLI's hand-built test PDFs

findings: list[dict[str, Any]] = []


def check(name: str, ok: bool, detail: Any = "") -> bool:
    findings.append({"check": name, "ok": ok, "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail != "" else ""))
    return ok


def note(name: str, detail: Any) -> None:
    findings.append({"check": name, "ok": None, "detail": detail})
    print(f"[ NOTE] {name} — {detail}")


def keys(entries: list[dict[str, Any]]) -> set[str]:
    return {str(entry["key"]) for entry in entries}


def created_key(response: dict[str, Any]) -> str:
    """The key of the one object a create call made."""
    successful = response.get("successful") or response.get("success") or {}
    if isinstance(successful, dict):
        first = next(iter(successful.values()))
        return str(first["key"] if isinstance(first, dict) else first)
    raise RuntimeError(f"create failed: {response}")


def main() -> int:
    env = dotenv_values(HERE.parent / ".env")
    group_id = str(env["ZOTERO_GROUP_ID"])
    z = zotero.Zotero(group_id, "group", str(env["ZOTERO_API_KEY"]))
    OUT.mkdir(exist_ok=True)

    info = z.key_info()
    access = info.get("access", {}).get("groups", {})
    group_access = access.get(group_id) or access.get("all") or {}
    if not check("key can write to the group", bool(group_access.get("write")), access):
        return 1

    created_items: list[str] = []
    created_collections: list[str] = []
    try:
        # --- starting point --------------------------------------------------------
        v0 = api.library_version(z)
        before = api.fetch_library_items(z)
        baseline = keys(before)
        note("starting library version", v0)
        note("items already in the group (left alone)", sorted(baseline))

        # --- create: a collection, a filed and an unfiled paper, a note, a PDF ------
        collection = created_key(z.create_collections([{"name": "[spike] Methods"}]))
        created_collections.append(collection)

        paper_a = z.item_template("journalArticle")
        paper_a.update(
            title="[spike] Paper A",
            date="2021-05",
            collections=[collection],
            tags=[{"tag": "RAG"}, {"tag": "spike"}],
            creators=[{"creatorType": "author", "firstName": "Ada", "lastName": "Lovelace"}],
        )
        key_a = created_key(z.create_items([paper_a]))
        created_items.append(key_a)

        paper_b = z.item_template("journalArticle")
        paper_b.update(title="[spike] Paper B (unfiled)", creators=[])
        key_b = created_key(z.create_items([paper_b]))
        created_items.append(key_b)

        child_note = z.item_template("note")
        child_note["note"] = "<p>[spike] A reader's note on paper A.</p>"
        key_note = created_key(z.create_items([child_note], parentid=key_a))
        created_items.append(key_note)

        pdf_path = OUT / "spike-paper-a.pdf"
        pdf_path.write_bytes(make_pdf_bytes(["Spike paper A, page one.", "Page two."]))
        uploaded = z.attachment_simple([str(pdf_path)], parentid=key_a)
        attached = (uploaded.get("success") or []) + (uploaded.get("unchanged") or [])
        check("PDF uploaded to the group", bool(attached), {k: len(v) for k, v in uploaded.items()})
        key_pdf = str(attached[0]["key"])
        created_items.append(key_pdf)

        v1 = api.library_version(z)
        check("library version advanced after the writes", v1 > v0, f"{v0} -> {v1}")

        # --- S1: whole-library fetch -------------------------------------------------
        everything = api.fetch_library_items(z)
        new = keys(everything) - baseline
        check(
            "whole-library fetch returns parents, children and unfiled items",
            new == {key_a, key_b, key_note, key_pdf},
            sorted(new),
        )
        by_key = {str(entry["key"]): entry for entry in everything}

        membership = library_membership(everything)
        check(
            "membership from each item's own collections",
            membership.get(key_a) == [collection] and membership.get(key_b) == [],
            {"A": membership.get(key_a), "B": membership.get(key_b)},
        )

        # --- S3: web links ------------------------------------------------------------
        links = {name: web_url(by_key[k]) for name, k in [("paper A", key_a), ("paper B", key_b),
                                                          ("note", key_note), ("PDF", key_pdf)]}
        note("links.alternate per item", links)
        check(
            "every item carries a group web link",
            all(f"/groups/{group_id}/" in link or "/groups/" in link for link in links.values()),
            "",
        )

        plan = build_plan([("AI_EDU_UPB", everything)], membership=membership)
        planned = {a.attachment_key: a for a in plan.attachments}
        check(
            "plan indexes the PDF and the note of paper A",
            {key_pdf, key_note} <= set(planned),
            sorted(planned),
        )
        if key_pdf in planned:
            entry = planned[key_pdf]
            check("planned citation URL is the API's link", entry.zotero_url == links["paper A"],
                  entry.zotero_url)
            check(
                "planned metadata: type, year, tags, folder",
                (entry.item_type, entry.year, entry.tags, entry.collection_keys)
                == ("journalArticle", "2021", ["RAG", "spike"], [collection]),
                [entry.item_type, entry.year, entry.tags, entry.collection_keys],
            )
        skipped = {s.key: s.reason.value for s in plan.skipped}
        note("skipped by the plan", skipped)

        for name in ("paper A", "PDF"):
            try:
                response = httpx.get(links[name], follow_redirects=True, timeout=20)
                note(
                    f"{name} link signed out",
                    {"status": response.status_code, "final_url": str(response.url),
                     "mentions login": "login" in str(response.url).lower()},
                )
            except httpx.HTTPError as exc:
                note(f"{name} link signed out", f"request failed: {exc}")

        # --- S1: file download ------------------------------------------------------------
        data = z.file(key_pdf)
        raw = data if isinstance(data, bytes) else str(data).encode()
        check("attachment downloads as a PDF", raw[:5] == b"%PDF-", f"{len(raw)} bytes")

        # --- S1: since= deltas -------------------------------------------------------------
        current_b = z.item(key_b)["data"]
        current_b["title"] = "[spike] Paper B (renamed)"
        z.update_item(current_b)
        current_a = z.item(key_a)["data"]
        current_a["tags"] = [*current_a["tags"], {"tag": "added-later"}]
        z.update_item(current_a)
        v2 = api.library_version(z)
        changed = keys(api.fetch_library_items(z, since=v1))
        check(
            "since= returns exactly the edited items (not their children, not old items)",
            changed == {key_a, key_b},
            sorted(changed),
        )
        note("child versions after editing the parent", {
            "note": z.item(key_note)["version"], "PDF": z.item(key_pdf)["version"], "v1": v1})

        check("nothing changed since the latest version", api.fetch_library_items(z, since=v2) == [],
              "")

        # --- S1: moving a parent to the trash ------------------------------------------------
        trashed_b = z.item(key_b)["data"]
        trashed_b["deleted"] = 1
        z.update_item(trashed_b)
        v3 = api.library_version(z)
        check("trashed item leaves /items", key_b not in keys(api.fetch_library_items(z)), "")
        check("trashed item is not in /items?since", key_b not in keys(api.fetch_library_items(z, since=v2)), "")
        deleted_now = z.deleted(since=v2)
        note("/deleted after trashing (items)", deleted_now.get("items"))
        removed = api.fetch_removed_item_keys(z, since=v2)
        check("fetch_removed_item_keys reports the trashed item", key_b in removed, sorted(removed))

        # --- S1: deleting a parent permanently -----------------------------------------------
        z.delete_item(z.item(key_a))
        v4 = api.library_version(z)
        remaining = keys(api.fetch_library_items(z))
        orphans = {key_note, key_pdf} & remaining
        note("children left after deleting their parent", sorted(orphans) or "none")
        removed = api.fetch_removed_item_keys(z, since=v3)
        note("/deleted after deleting paper A", sorted(removed))
        check("deleted parent reported", key_a in removed, "")
        check(
            "its children are reported too (or were deleted with it)",
            {key_note, key_pdf} <= removed or not orphans,
            {"reported": sorted({key_note, key_pdf} & removed), "still present": sorted(orphans)},
        )

        # --- S1: the library version as the sync cursor ----------------------------------------
        z.delete_collection(z.collection(collection))
        created_collections.remove(collection)
        v5 = api.library_version(z)
        deleted_collections = z.deleted(since=v4).get("collections")
        check("deleted collection reported in /deleted", collection in (deleted_collections or []),
              deleted_collections)
        note("versions", {"v0": v0, "v1": v1, "v2": v2, "v3": v3, "v4": v4, "v5": v5})
        check("pre-existing items untouched", baseline <= keys(api.fetch_library_items(z)), "")
    finally:
        # Delete whatever is left of what this run created, children first.
        for key in reversed(created_items):
            try:
                z.delete_item(z.item(key))
            except Exception as exc:  # noqa: BLE001 - already gone is fine
                if "404" not in str(exc) and "Not found" not in str(exc):
                    print(f"cleanup: could not delete {key}: {exc}")
        for key in created_collections:
            try:
                z.delete_collection(z.collection(key))
            except Exception as exc:  # noqa: BLE001
                print(f"cleanup: could not delete collection {key}: {exc}")
        time.sleep(1)
        left = [k for k in created_items if k in keys(z.everything(z.items()))
                or k in keys(z.everything(z.trash()))]
        check("cleanup: nothing created by the spike remains", not left, left)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        (OUT / f"s1_s3_{stamp}.json").write_text(json.dumps(findings, indent=2, default=str))

    failed = [f["check"] for f in findings if f["ok"] is False]
    print(f"\n{len(failed)} failed" + (f": {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
