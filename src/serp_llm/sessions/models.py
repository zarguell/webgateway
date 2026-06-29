from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CookieEntry:
    name: str
    value: str
    domain: str
    path: str = "/"
    expiry: float | None = None
    secure: bool = True
    http_only: bool = True


@dataclass
class SessionData:
    """Full session state — serialised to encrypted JSON on disk."""

    session_id: str
    browser_service: str
    domain: str
    cookies: list[CookieEntry]
    user_agent: str
    fingerprint_id: str
    created_ts: float
    last_used_ts: float
    expiry_ts: float | None = None
    proxy_binding: str | None = None
    strict_proxy: bool = False
    use_count: int = 0
    local_storage: dict[str, str] | None = None


@dataclass
class SessionInfo:
    """Public metadata — no cookie values or local_storage."""

    session_id: str
    domain: str
    browser: str
    engine: str
    created_ts: float
    last_used_ts: float
    expiry: float | None = None
    proxy_binding: str | None = None
    strict_proxy: bool = False
    cookie_count: int = 0
    use_count: int = 0


def session_to_info(data: SessionData) -> SessionInfo:
    """Convert full SessionData to public SessionInfo (strips secrets)."""
    return SessionInfo(
        session_id=data.session_id,
        domain=data.domain,
        browser=data.browser_service,
        engine="firefox",
        created_ts=data.created_ts,
        last_used_ts=data.last_used_ts,
        expiry=data.expiry_ts,
        proxy_binding=data.proxy_binding,
        strict_proxy=data.strict_proxy,
        cookie_count=len(data.cookies),
        use_count=data.use_count,
    )
