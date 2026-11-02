"""What the UI shows about the library: its size, sync state, folders and tags."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from zotero_rag_web.api.deps import Connection

router = APIRouter(prefix="/api", tags=["library"])


@router.get("/library")
def library(conn: Connection) -> dict[str, Any]:
    row = conn.execute(
        "SELECT (SELECT count(*) FROM items), (SELECT count(*) FROM chunks), "
        "(SELECT min(year_int) FROM items WHERE year_int > 0), "
        "(SELECT max(year_int) FROM items WHERE year_int > 0), "
        "l.name, l.web_url, l.last_sync_at, l.last_sync_status "
        "FROM (SELECT 1) one LEFT JOIN library l ON l.id = 1"
    ).fetchone()
    assert row is not None
    items, chunks, year_min, year_max, name, web_url, synced_at, sync_status = row
    return {
        "name": name,
        "web_url": web_url,
        "items": items,
        "chunks": chunks,
        "indexed": chunks > 0,
        "year_min": year_min,
        "year_max": year_max,
        "last_sync_at": synced_at,
        "last_sync_status": sync_status,
    }


@router.get("/collections")
def collections(conn: Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT key, parent_key, name, path, item_count FROM collections ORDER BY lower(path)"
    ).fetchall()
    return [
        {"key": key, "parent_key": parent, "name": name, "path": path, "item_count": count}
        for key, parent, name, path, count in rows
    ]


@router.get("/tags")
def tags(conn: Connection, q: str = Query(default="", max_length=100)) -> list[dict[str, Any]]:
    """The library's tags, most used first; `q` keeps those containing it."""
    rows = conn.execute(
        "SELECT tag, count(*) AS n FROM items, unnest(tags) AS tag "
        "WHERE %s = '' OR tag ILIKE '%%' || %s || '%%' "
        "GROUP BY tag ORDER BY n DESC, lower(tag) LIMIT 20",
        (q, q),
    ).fetchall()
    return [{"tag": tag, "items": n} for tag, n in rows]
