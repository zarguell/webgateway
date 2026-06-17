from __future__ import annotations

import time
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from webgateway.sessions.manager import SessionError, SessionManager
from webgateway.sessions.models import CookieEntry, SessionData
from webgateway.sessions.store import SessionStore


@pytest.fixture
def store_path(tmp_path: Path) -> str:
    return str(tmp_path / "sessions")


@pytest.fixture
def key() -> str:
    return Fernet.generate_key().decode()


@pytest.fixture
def store(store_path: str, key: str) -> SessionStore:
    return SessionStore(store_path, key)


@pytest.fixture
def manager(store: SessionStore) -> SessionManager:
    from webgateway.config import SessionsConfig
    return SessionManager(
        store,
        SessionsConfig(
            auto_invalidate_on_login_wall=True,
            strict_proxy_binding=True,
            login_wall_patterns=["Sign in", "Subscribe to read"],
        ),
    )


def _make_session(session_id: str = "sess_integ_001", **overrides) -> SessionData:
    defaults = dict(
        session_id=session_id,
        browser_service="invisible_playwright",
        domain="wsj.com",
        cookies=[CookieEntry(name="sid", value="abc", domain="wsj.com")],
        user_agent="Mozilla/5.0 Firefox/150.0",
        fingerprint_id="fp_01",
        created_ts=time.time(),
        last_used_ts=time.time(),
        expiry_ts=time.time() + 86400,
    )
    defaults.update(overrides)
    return SessionData(**defaults)


class TestSessionIntegration:
    async def test_resolve_updates_use_count(self, manager: SessionManager, store: SessionStore):
        store.save(_make_session("sess_count"))
        await manager.resolve(
            "sess_count",
            provider_name="invisible_playwright",
            domain="wsj.com",
            proxy_name=None,
        )
        session = store.load("sess_count")
        assert session.use_count == 1

    async def test_resolve_subdomain_matches(self, manager: SessionManager, store: SessionStore):
        store.save(_make_session("sess_sub", domain="wsj.com"))
        resolved = await manager.resolve(
            "sess_sub",
            provider_name="invisible_playwright",
            domain="www.wsj.com",
            proxy_name=None,
        )
        assert resolved is not None

    async def test_invalidate_expired_session(self, manager: SessionManager, store: SessionStore):
        store.save(_make_session("sess_exp", expiry_ts=time.time() - 1))
        with pytest.raises(SessionError) as exc:
            await manager.resolve(
                "sess_exp",
                provider_name="invisible_playwright",
                domain="wsj.com",
                proxy_name=None,
            )
        assert exc.value.error_class == "session_expired"
        assert not store.exists("sess_exp")

    async def test_browser_mismatch(self, manager: SessionManager, store: SessionStore):
        store.save(_make_session("sess_bm", browser_service="camoufox"))
        with pytest.raises(SessionError) as exc:
            await manager.resolve(
                "sess_bm",
                provider_name="invisible_playwright",
                domain="wsj.com",
                proxy_name=None,
            )
        assert exc.value.error_class == "session_browser_mismatch"

    async def test_invalidate_by_browser(self, manager: SessionManager, store: SessionStore):
        store.save(_make_session("sess_a", browser_service="invisible_playwright"))
        store.save(_make_session("sess_b", browser_service="camoufox"))
        count = await manager.invalidate(browser="camoufox")
        assert count == 1
        assert store.exists("sess_a")
        assert not store.exists("sess_b")

    async def test_proxy_binding_enforced(self, manager: SessionManager, store: SessionStore):
        store.save(_make_session(
            "sess_proxy",
            proxy_binding="residential_us",
            strict_proxy=True,
        ))
        with pytest.raises(SessionError) as exc:
            await manager.resolve(
                "sess_proxy",
                provider_name="invisible_playwright",
                domain="wsj.com",
                proxy_name="bad_proxy",
            )
        assert exc.value.error_class == "session_proxy_mismatch"

    async def test_proxy_binding_not_required_when_not_strict(
        self, manager: SessionManager, store: SessionStore
    ):
        store.save(_make_session(
            "sess_nonstrict",
            proxy_binding="residential_us",
            strict_proxy=False,
        ))
        resolved = await manager.resolve(
            "sess_nonstrict",
            provider_name="invisible_playwright",
            domain="wsj.com",
            proxy_name=None,
        )
        assert resolved is not None

    async def test_missing_session_raises(self, manager: SessionManager):
        with pytest.raises(SessionError) as exc:
            await manager.resolve(
                "nonexistent",
                provider_name="invisible_playwright",
                domain="wsj.com",
                proxy_name=None,
            )
        assert exc.value.error_class == "session_not_found"

    async def test_multiple_sessions_invalidated_by_domain(
        self, manager: SessionManager, store: SessionStore
    ):
        store.save(_make_session("sess_a", domain="wsj.com"))
        store.save(_make_session("sess_b", domain="wsj.com"))
        store.save(_make_session("sess_c", domain="nytimes.com"))
        count = await manager.invalidate(domain="wsj.com")
        assert count == 2
        assert not store.exists("sess_a")
        assert not store.exists("sess_b")
        assert store.exists("sess_c")

    async def test_touch_nonexistent_is_noop(self, manager: SessionManager):
        await manager.touch("nonexistent")  # should not raise
