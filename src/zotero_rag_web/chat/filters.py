"""What the UI sends to narrow a search, turned into zotero-rag-core's `Filters`.

The UI picks one collection; a search there covers it and every collection below it,
as the CLI's terminal UI does (`snapshot.subtree_keys`). The tree comes from the
`collections` table that each sync rewrites.
"""

from __future__ import annotations

from typing import Any

import psycopg
from pydantic import BaseModel, ConfigDict, Field, model_validator

from zotero_rag_core.store import Filters

#: A collection and its descendants, by key, from the synced tree.
_SUBTREE = """
WITH RECURSIVE subtree(key) AS (
    SELECT key FROM collections WHERE key = %s
    UNION
    SELECT c.key FROM collections c JOIN subtree s ON c.parent_key = s.key
)
SELECT key FROM subtree
"""


class UnknownCollectionError(LookupError):
    """The collection is not in the library (any more)."""


class SearchFilters(BaseModel):
    """The filters of a conversation, as the API carries them (PRD §4.5)."""

    model_config = ConfigDict(extra="forbid")

    #: A collection key; its subcollections are included. None: the whole library.
    collection: str | None = None
    year_min: int | None = Field(default=None, ge=0, le=9999)
    year_max: int | None = Field(default=None, ge=0, le=9999)
    include_undated: bool = True
    #: Items with any of these tags. Empty or None: no tag condition.
    tags: list[str] | None = None

    @model_validator(mode="after")
    def _ordered(self) -> SearchFilters:
        low, high = self.year_min, self.year_max
        if low is not None and high is not None and low > high:
            raise ValueError("year_min is after year_max")
        return self

    @property
    def is_empty(self) -> bool:
        return (
            self.collection is None
            and self.year_min is None
            and self.year_max is None
            and not self.tags
        )


def subtree(conn: psycopg.Connection[Any], collection: str) -> frozenset[str]:
    rows = conn.execute(_SUBTREE, (collection,)).fetchall()
    if not rows:
        raise UnknownCollectionError(collection)
    return frozenset(str(row[0]) for row in rows)


def to_core(filters: SearchFilters, conn: psycopg.Connection[Any]) -> Filters | None:
    """The core's filters, or None to search everything.

    An empty tag list means "no tag condition", not "nothing": the UI sends what
    its picker holds, and an emptied picker should widen the search, not blank it.
    """
    if filters.is_empty:
        return None
    return Filters(
        collection_keys=subtree(conn, filters.collection) if filters.collection else None,
        year_min=filters.year_min,
        year_max=filters.year_max,
        include_undated=filters.include_undated,
        tags=frozenset(filters.tags) if filters.tags else None,
    )
