from __future__ import annotations

import time

from serp_llm.sessions.models import SessionData
from serp_llm.sessions.store import SessionNotFound, SessionStore


class SessionError(Exception):
    """Raised when a session cannot be resolved or is invalid.

    Attributes:
        error_class: Machine-readable error type string.
        session_id: The session ID that caused the error, if applicable.
    """

    def __init__(
        self,
        error_class: str,
        message: str,
        *,
        session_id: str | None = None,
    ) -> None:
        self.error_class = error_class
        self.session_id = session_id
        super().__init__(f"[{error_class}] {message}")


class SessionManager:
    """Session lifecycle management — wraps store with validation."""

    def __init__(
        self,
        store: SessionStore,
        config: object,
    ) -> None:
        self._store = store
        self._config = config

    async def resolve(
        self,
        session_id: str,
        *,
        provider_name: str,
        domain: str,
        proxy_name: str | None,
    ) -> SessionData:
        """Load session, validate all bindings."""
        try:
            session = self._store.load(session_id)
        except SessionNotFound:
            raise SessionError(
                "session_not_found",
                f"Session {session_id!r} not found",
                session_id=session_id,
            ) from None

        now = time.time()

        if session.expiry_ts is not None and now > session.expiry_ts:
            self._store.delete(session_id)
            raise SessionError(
                "session_expired",
                f"Session {session_id!r} expired",
                session_id=session_id,
            )

        if session.browser_service != provider_name:
            raise SessionError(
                "session_browser_mismatch",
                f"Session {session_id!r} is bound to {session.browser_service!r}, "
                f"but request uses {provider_name!r}",
                session_id=session_id,
            )

        if not _domain_matches(session.domain, domain):
            raise SessionError(
                "session_domain_mismatch",
                f"Session {session_id!r} is bound to domain {session.domain!r}, "
                f"but request domain is {domain!r}",
                session_id=session_id,
            )

        if (
            session.strict_proxy
            and session.proxy_binding is not None
            and proxy_name != session.proxy_binding
        ):
            raise SessionError(
                "session_proxy_mismatch",
                f"Session {session_id!r} requires proxy {session.proxy_binding!r}, "
                f"but request resolves to {proxy_name!r}",
                session_id=session_id,
            )

        session.last_used_ts = now
        session.use_count += 1
        self._store.save(session)

        return session

    async def invalidate(
        self,
        *,
        session_id: str | None = None,
        domain: str | None = None,
        browser: str | None = None,
    ) -> int:
        """Invalidate matching sessions. Returns count."""
        if session_id is not None:
            self._store.delete(session_id)
            return 1

        count = 0
        for info in self._store.list_sessions():
            match = True
            if domain is not None and not _domain_matches(info.domain, domain):
                match = False
            if browser is not None and info.browser != browser:
                match = False
            if match:
                self._store.delete(info.session_id)
                count += 1
        return count

    async def touch(self, session_id: str) -> None:
        """Update last_used_ts and increment use_count."""
        try:
            session = self._store.load(session_id)
        except SessionNotFound:
            return
        session.last_used_ts = time.time()
        session.use_count += 1
        self._store.save(session)


def _domain_matches(session_domain: str, request_domain: str) -> bool:
    """Check if request_domain matches session_domain (exact or subdomain).

    A session bound to "wsj.com" matches "www.wsj.com" and "wsj.com".
    """
    return session_domain == request_domain or request_domain.endswith("." + session_domain)
