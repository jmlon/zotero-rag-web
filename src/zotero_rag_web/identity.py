"""Who is asking, without accounts (PRD §8.2): a random id in a signed cookie.

The signature stops one browser from claiming another's id, and so its
conversations. It is privacy by obscurity, not authentication: anyone with the URL
can use the app. When sign-in arrives (PRD §11.2), a user is attached to the
browser id, and its history follows.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid

from itsdangerous import BadSignature, URLSafeSerializer

logger = logging.getLogger(__name__)

COOKIE_NAME = "zrw_bid"
#: The longest lifetime browsers honour for a cookie.
COOKIE_MAX_AGE = 400 * 24 * 3600


class Identity:
    def __init__(self, secret: str | None) -> None:
        if not secret:
            logger.warning(
                "SECRET_KEY is not set: using a temporary one, so every browser loses its "
                "conversations when the server restarts. Set SECRET_KEY."
            )
            secret = secrets.token_hex(32)
        self._secret = secret
        self._signer = URLSafeSerializer(secret, salt="zrw-browser-id")

    def read(self, cookie: str | None) -> uuid.UUID | None:
        """The browser id in a cookie, or None if absent, tampered with or malformed."""
        if not cookie:
            return None
        try:
            return uuid.UUID(str(self._signer.loads(cookie)))
        except (BadSignature, ValueError):
            return None

    def issue(self, browser_id: uuid.UUID) -> str:
        return self._signer.dumps(str(browser_id))

    def ip_hash(self, ip: str | None) -> str | None:
        """A salted hash for the per-IP limit: the raw address is never stored."""
        if not ip:
            return None
        return hashlib.sha256(f"{self._secret}|{ip}".encode()).hexdigest()[:32]
