"""One sync at a time, across processes and machines: a PostgreSQL advisory lock."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg

#: Arbitrary but fixed: "zrwi" in ASCII.
SYNC_LOCK_KEY = 0x7A727769


@contextmanager
def sync_lock(database_url: str) -> Iterator[bool]:
    """Yields whether this process holds the lock; held until the block ends.

    A session-level lock on a connection of its own, so it is released when the
    process dies, even if nothing gets to clean up.
    """
    with psycopg.connect(database_url, autocommit=True) as conn:
        row = conn.execute("SELECT pg_try_advisory_lock(%s)", (SYNC_LOCK_KEY,)).fetchone()
        acquired = bool(row and row[0])
        try:
            yield acquired
        finally:
            if acquired:
                conn.execute("SELECT pg_advisory_unlock(%s)", (SYNC_LOCK_KEY,))
