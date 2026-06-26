"""Admin UI session management — signed httpOnly cookies.

Uses ``itsdangerous.URLSafeTimedSerializer`` to create signed, time-limited
session cookies. The cookie payload is ``{"key_id": str, "role": str}`` —
no server-side session storage needed.

The signing secret comes from the ``ADMIN_SESSION_SECRET`` environment variable.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from datetime import timedelta

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

__all__ = ["AdminSession", "AdminSessionManager"]

logger = logging.getLogger(__name__)

_SESSION_TTL = timedelta(hours=24)
_CSRF_TOKEN_TTL_SECONDS = 3600  # 1 hour
_DEFAULT_SECRET_VAR = "ADMIN_SESSION_SECRET"
_COOKIE_NAME = "admin_session"
_COOKIE_PATH = "/admin"


class AdminSession:
    """Represents an authenticated admin session decoded from a cookie."""

    def __init__(self, key_id: str, role: str) -> None:
        self.key_id = key_id
        self.role = role

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


class AdminSessionManager:
    """Creates and verifies signed admin session cookies.

    Args:
        secret: Signing key. Falls back to ``ADMIN_SESSION_SECRET`` env var.
    """

    def __init__(self, secret: str | None = None) -> None:
        resolved = secret or os.environ.get(_DEFAULT_SECRET_VAR)
        if not resolved:
            logger.warning(
                "ADMIN_SESSION_SECRET not set — using ephemeral random key. "
                "All existing sessions will be invalidated on restart."
            )
            import secrets
            resolved = secrets.token_urlsafe(32)
        self._signing_key = resolved
        self._serializer = URLSafeTimedSerializer(
            resolved, salt="admin-session"
        )

    def create_session(
        self, key_id: str, role: str = "admin"
    ) -> str:
        """Create a signed session cookie value.

        Returns the cookie value to set (already signed).
        """
        data = {"key_id": key_id, "role": role}
        return self._serializer.dumps(data)

    def verify_session(
        self, cookie_value: str | None
    ) -> AdminSession | None:
        """Verify and decode a session cookie.

        Returns an ``AdminSession`` if valid, or ``None`` if the cookie is
        missing, expired, or tampered with.
        """
        if not cookie_value:
            return None
        try:
            data = self._serializer.loads(
                cookie_value, max_age=int(_SESSION_TTL.total_seconds())
            )
        except (BadSignature, SignatureExpired):
            return None
        key_id = data.get("key_id")
        role = data.get("role", "admin")
        if not key_id:
            return None
        return AdminSession(key_id=key_id, role=role)

    @property
    def cookie_name(self) -> str:
        return _COOKIE_NAME

    @property
    def cookie_path(self) -> str:
        return _COOKIE_PATH

    @property
    def cookie_max_age(self) -> int:
        return int(_SESSION_TTL.total_seconds())

    # ------------------------------------------------------------------
    # CSRF token methods
    # ------------------------------------------------------------------

    def generate_csrf_token(self, session_cookie: str) -> str:
        """Generate a signed CSRF token tied to the admin session.

        The token is an HMAC-SHA256 of the session cookie value + timestamp,
        preventing reuse after expiry.
        """
        expires = str(int(time.time()) + _CSRF_TOKEN_TTL_SECONDS)
        msg = f"{session_cookie}:{expires}"
        sig = hmac.new(
            self._signing_key.encode(),
            msg.encode(),
            hashlib.sha256,
        ).hexdigest()
        return f"{expires}.{sig}"

    def verify_csrf_token(self, session_cookie: str, token: str) -> bool:
        """Verify a CSRF token is valid and not expired.

        Returns True if the token matches and is within the TTL window.
        """
        try:
            expires_ts_str, sig = token.split(".", 1)
            expires = int(expires_ts_str)
        except (ValueError, IndexError):
            return False
        if time.time() > expires:
            return False
        msg = f"{session_cookie}:{expires_ts_str}"
        expected = hmac.new(
            self._signing_key.encode(),
            msg.encode(),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(sig, expected)
