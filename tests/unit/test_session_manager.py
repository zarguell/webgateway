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
    return SessionManager(
        store,
        type("Config", (), {
            "auto_invalidate_on_login_wall": True,
            "strict_proxy_binding": True,
            "login_wall_patterns": [],
        })(),
    )


def _make_session(session_id: str = "sess_001", **overrides) -> SessionData:
    defaults = dict(
        session_id=session_id,
        browser_service="invisible_playwright",
        domain="example.com",
        cookies=[CookieEntry(name="sid", value="abc", domain="example.com")],
        user_agent="Mozilla/5.0 Firefox/150.0",
        fingerprint_id="fp_01",
        created_ts=time.time(),
        last_used_ts=time.time(),
        expiry_ts=time.time() + 86400,
    )
    defaults.update(overrides)
    return SessionData(**defaults)


class TestSessionManager:
    async def test_resolve_valid(self, manager: SessionManager, store: SessionStore):
        session = _make_session()
        store.save(session)
        resolved = await manager.resolve(
            "sess_001",
            provider_name="invisible_playwright",
            domain="example.com",
            proxy_name=None,
        )
        assert resolved.session_id == "sess_001"

    async def test_resolve_expired_raises(self, manager: SessionManager, store: SessionStore):
        session = _make_session(expiry_ts=time.time() - 1)
        store.save(session)
        with pytest.raises(SessionError) as exc:
            await manager.resolve(
                "sess_001",
                provider_name="invisible_playwright",
                domain="example.com",
                proxy_name=None,
            )
        assert exc.value.error_class == "session_expired"

    async def test_resolve_browser_mismatch_raises(self, manager: SessionManager, store: SessionStore):
        session = _make_session(browser_service="camoufox")
        store.save(session)
        with pytest.raises(SessionError) as exc:
            await manager.resolve(
                "sess_001",
                provider_name="invisible_playwright",
                domain="example.com",
                proxy_name=None,
            )
        assert exc.value.error_class == "session_browser_mismatch"

    async def test_resolve_domain_mismatch_raises(self, manager: SessionManager, store: SessionStore):
        session = _make_session(domain="other.com")
        store.save(session)
        with pytest.raises(SessionError) as exc:
            await manager.resolve(
                "sess_001",
                provider_name="invisible_playwright",
                domain="example.com",
                proxy_name=None,
            )
        assert exc.value.error_class == "session_domain_mismatch"

    async def test_resolve_proxy_mismatch_raises(self, manager: SessionManager, store: SessionStore):
        session = _make_session(proxy_binding="residential_us", strict_proxy=True)
        store.save(session)
        with pytest.raises(SessionError) as exc:
            await manager.resolve(
                "sess_001",
                provider_name="invisible_playwright",
                domain="example.com",
                proxy_name="different_proxy",
            )
        assert exc.value.error_class == "session_proxy_mismatch"

    async def test_touch_updates_ts(self, manager: SessionManager, store: SessionStore):
        session = _make_session(use_count=0)
        store.save(session)
        await manager.touch("sess_001")
        loaded = store.load("sess_001")
        assert loaded.use_count == 1

    async def test_invalidate_by_id(self, manager: SessionManager, store: SessionStore):
        store.save(_make_session("sess_a"))
        store.save(_make_session("sess_b"))
        count = await manager.invalidate(session_id="sess_a")
        assert count == 1
        assert not store.exists("sess_a")
        assert store.exists("sess_b")

    async def test_invalidate_by_domain(self, manager: SessionManager, store: SessionStore):
        store.save(_make_session("sess_a", domain="example.com"))
        store.save(_make_session("sess_b", domain="other.com"))
        count = await manager.invalidate(domain="example.com")
        assert count == 1
        assert not store.exists("sess_a")
        assert store.exists("sess_b")

    async def test_invalidate_by_browser(self, manager: SessionManager, store: SessionStore):
        store.save(_make_session("sess_a", browser_service="invisible_playwright"))
        store.save(_make_session("sess_b", browser_service="camoufox"))
        count = await manager.invalidate(browser="camoufox")
        assert count == 1
        assert store.exists("sess_a")
        assert not store.exists("sess_b")

    async def test_invalidate_nonexistent(self, manager: SessionManager):
        count = await manager.invalidate(session_id="nonexistent")
        assert count == 1

    async def test_resolve_subdomain_match(self, manager: SessionManager, store: SessionStore):
        store.save(_make_session("sess_sub", domain="wsj.com"))
        resolved = await manager.resolve(
            "sess_sub",
            provider_name="invisible_playwright",
            domain="www.wsj.com",
            proxy_name=None,
        )
        assert resolved.session_id == "sess_sub"
