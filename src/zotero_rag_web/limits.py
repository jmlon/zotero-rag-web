"""Rate limits and the daily spend cap (PRD §8.3), counted from `messages`.

No Redis: a small team asks a few hundred questions a day, and the messages table
already records who asked what, when, and at what cost.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import psycopg

from zotero_rag_web.settings import Settings

#: Assumed cost of a question still being answered, so a burst of simultaneous
#: questions cannot all slip under the cap before any of them is priced.
IN_FLIGHT_ESTIMATE_USD = 0.002
WINDOW = timedelta(hours=1)


@dataclass
class LimitExceeded(Exception):
    code: Literal["rate_limited", "budget_exhausted"]
    retry_after_s: int
    message: str


def check(
    conn: psycopg.Connection[Any],
    settings: Settings,
    browser_id: uuid.UUID,
    ip_hash: str | None,
    now: datetime | None = None,
) -> None:
    """Raise `LimitExceeded` if this question would go over a limit."""
    now = now or datetime.now(UTC)
    since = now - WINDOW
    if settings.rate_limit_per_browser_hour:
        count, oldest = conn.execute(
            "SELECT count(*), min(m.created_at) FROM messages m "
            "JOIN conversations c ON c.id = m.conversation_id "
            "WHERE c.browser_id = %s AND m.role = 'user' AND m.created_at > %s",
            (browser_id, since),
        ).fetchone() or (0, None)
        if count >= settings.rate_limit_per_browser_hour:
            raise _rate_limited(oldest, now)
    if settings.rate_limit_per_ip_hour and ip_hash:
        count, oldest = conn.execute(
            "SELECT count(*), min(created_at) FROM messages "
            "WHERE role = 'user' AND ip_hash = %s AND created_at > %s",
            (ip_hash, since),
        ).fetchone() or (0, None)
        if count >= settings.rate_limit_per_ip_hour:
            raise _rate_limited(oldest, now)
    if settings.daily_spend_cap_usd:
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        spent, in_flight = conn.execute(
            "SELECT coalesce(sum((usage->>'total_cost')::numeric), 0), "
            "count(*) FILTER (WHERE status = 'streaming') "
            "FROM messages WHERE role = 'assistant' AND created_at >= %s",
            (midnight,),
        ).fetchone() or (0, 0)
        if float(spent) + in_flight * IN_FLIGHT_ESTIMATE_USD >= settings.daily_spend_cap_usd:
            reset = midnight + timedelta(days=1)
            raise LimitExceeded(
                "budget_exhausted",
                int((reset - now).total_seconds()) + 1,
                "The team's daily question budget is used up. It resets at 00:00 UTC.",
            )


def _rate_limited(oldest: datetime | None, now: datetime) -> LimitExceeded:
    free_at = (oldest or now) + WINDOW
    wait = max(1, int((free_at - now).total_seconds()) + 1)
    return LimitExceeded(
        "rate_limited",
        wait,
        "You've asked a lot of questions in a short time. Try again in a few minutes.",
    )
