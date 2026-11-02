"""The web app's own record of the library: items, collections, the sync cursor, runs.

The chunk store (zotero-rag-core's PgStore) writes chunks and the item fields a
citation needs. This module writes what the store does not know: every item of the
library (with or without a file), its Zotero version and date, the collection tree,
and a row per sync run.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from zotero_rag_core.metadata import authors_str, item_tags, parse_year, year_number
from zotero_rag_core.zotero_api import CollectionNode

ZOTERO_WEB = "https://www.zotero.org"


def item_url(group_id: int, item_key: str) -> str:
    """The item on zotero.org, by group *id*: the slug in the API's own link breaks
    when the group is renamed, and nothing would re-sync to fix it (S3)."""
    return f"{ZOTERO_WEB}/groups/{group_id}/items/{item_key}"


def is_parent(entry: Mapping[str, Any]) -> bool:
    data = entry.get("data") or {}
    return data.get("itemType") not in ("attachment", "note", "annotation")


def item_row(entry: Mapping[str, Any], group_id: int) -> dict[str, Any]:
    """An `items` row from an API payload, with the core's own field derivations."""
    data = entry.get("data") or {}
    key = str(data.get("key") or entry.get("key"))
    year = parse_year(data.get("date"))
    collections = sorted({str(k) for k in data.get("collections") or []})
    tags = item_tags(data)
    return {
        "key": key,
        "item_type": data.get("itemType") or "",
        "title": data.get("title") or "(untitled)",
        "authors": authors_str(data),
        "year": year,
        "year_int": year_number(year),
        "doi": data.get("DOI") or "",
        "zotero_url": item_url(group_id, key),
        # NULL, not empty, as the store writes them: an empty list matches no filter.
        "collection_keys": collections or None,
        "tags": tags or None,
        "version": int(data.get("version") or entry.get("version") or 0),
        "date": data.get("date") or "",
    }


def upsert_items(conn: psycopg.Connection[Any], rows: Iterable[dict[str, Any]]) -> int:
    rows = list(rows)
    if not rows:
        return 0
    columns = list(rows[0])
    assignments = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns if c != "key")
    with conn.cursor() as cur:
        cur.executemany(
            f"INSERT INTO items ({', '.join(columns)}, updated_at) "  # noqa: S608 - fixed names
            f"VALUES ({', '.join(f'%({c})s' for c in columns)}, now()) "
            f"ON CONFLICT (key) DO UPDATE SET {assignments}, updated_at = now()",
            rows,
        )
    return len(rows)


def delete_items_except(conn: psycopg.Connection[Any], keep: set[str]) -> int:
    """Forget items no longer in the library; their attachments and chunks cascade."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM items WHERE NOT (key = ANY(%s::text[]))", (sorted(keep),))
        return cur.rowcount


def replace_collections(conn: psycopg.Connection[Any], roots: list[CollectionNode]) -> int:
    """Rewrite the collection tree: every node with its parent and its path."""
    rows: list[dict[str, Any]] = []

    def walk(node: CollectionNode, parent: str | None, prefix: str) -> None:
        path = f"{prefix}/{node.name}" if prefix else node.name
        rows.append(
            {
                "key": node.key,
                "parent_key": parent,
                "name": node.name,
                "path": path,
                "item_count": node.num_items,
            }
        )
        for child in node.children:
            walk(child, node.key, path)

    for root in roots:
        walk(root, None, "")
    with conn.cursor() as cur:
        cur.execute("DELETE FROM collections")
        if rows:
            cur.executemany(
                "INSERT INTO collections (key, parent_key, name, path, item_count) "
                "VALUES (%(key)s, %(parent_key)s, %(name)s, %(path)s, %(item_count)s)",
                rows,
            )
    return len(rows)


def last_version(conn: psycopg.Connection[Any]) -> int | None:
    row = conn.execute("SELECT last_version FROM library WHERE id = 1").fetchone()
    return None if row is None or row[0] is None else int(row[0])


def record_library(
    conn: psycopg.Connection[Any],
    *,
    group_id: int,
    name: str | None,
    status: str,
    version: int | None,
) -> None:
    """The sync's outcome. The cursor only moves when `version` is given: a run that
    stopped short leaves it, so the next run looks at the same changes again."""
    conn.execute(
        "INSERT INTO library (id, group_id, name, web_url, last_version, last_sync_at, "
        "last_sync_status) VALUES (1, %(group_id)s, %(name)s, %(web_url)s, %(version)s, "
        "now(), %(status)s) ON CONFLICT (id) DO UPDATE SET group_id = EXCLUDED.group_id, "
        "name = coalesce(EXCLUDED.name, library.name), web_url = EXCLUDED.web_url, "
        "last_version = coalesce(EXCLUDED.last_version, library.last_version), "
        "last_sync_at = now(), last_sync_status = EXCLUDED.last_sync_status",
        {
            "group_id": group_id,
            "name": name,
            "web_url": f"{ZOTERO_WEB}/groups/{group_id}",
            "version": version,
            "status": status,
        },
    )


def forget_cursor(conn: psycopg.Connection[Any]) -> None:
    """Make the next sync look at the whole library again (after a rechunk)."""
    conn.execute("UPDATE library SET last_version = NULL WHERE id = 1")


def start_run(conn: psycopg.Connection[Any], trigger: str) -> int:
    row = conn.execute(
        "INSERT INTO index_runs (trigger, status) VALUES (%s, 'running') RETURNING id",
        (trigger,),
    ).fetchone()
    assert row is not None
    return int(row[0])


def finish_run(
    conn: psycopg.Connection[Any],
    run_id: int,
    status: str,
    stats: Mapping[str, Any],
    failures: list[dict[str, Any]],
) -> None:
    conn.execute(
        "UPDATE index_runs SET finished_at = %s, status = %s, stats = %s, failures = %s "
        "WHERE id = %s",
        (datetime.now(UTC), status, Jsonb(dict(stats)), Jsonb(failures), run_id),
    )
