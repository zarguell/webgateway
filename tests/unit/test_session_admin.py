from __future__ import annotations

import time
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from webgateway.auth import verify_admin
from webgateway.config import AuthKey
from webgateway.main import create_app
from webgateway.sessions.manager import SessionManager
from webgateway.sessions.models import CookieEntry, SessionData
from webgateway.sessions.store import SessionStore


@pytest.fixture
def store_path(tmp_path: Path) -> str:
    p = tmp_path / "sessions"
    p.mkdir(parents=True)
    return str(p)


@pytest.fixture
def key() -> str:
    return Fernet.generate_key().decode()


@pytest.fixture
def store(store_path: str, key: str) -> SessionStore:
    return SessionStore(store_path, key)


@pytest.fixture
def app(store: SessionStore) -> TestClient:
    application = create_app()
    application.state.session_store = store
    application.state.session_manager = SessionManager(
        store,
        type("Config", (), {
            "auto_invalidate_on_login_wall": True,
            "strict_proxy_binding": True,
            "login_wall_patterns": ["Sign in"],
        })(),
    )

    # Override auth for testing
    application.dependency_overrides[verify_admin] = lambda: AuthKey(
        id="test_admin", secret="test", admin=True
    )

    return TestClient(application)


def _make_session(session_id: str = "sess_test_001") -> SessionData:
    return SessionData(
        session_id=session_id,
        browser_service="invisible_playwright",
        domain="example.com",
        cookies=[CookieEntry(name="sid", value="abc", domain="example.com")],
        user_agent="Mozilla/5.0 Firefox/150.0",
        fingerprint_id="fp_01",
        created_ts=time.time(),
        last_used_ts=time.time(),
    )


class TestSessionAdmin:
    def test_create_session(self, app: TestClient):
        resp = app.post(
            "/admin/sessions/create",
            json={
                "session_id": "sess_test_001",
                "browser": "invisible_playwright",
                "domain": "example.com",
                "cookies": [{"name": "sid", "value": "abc", "domain": "example.com"}],
                "user_agent": "Mozilla/5.0 Firefox/150.0",
                "fingerprint_id": "fp_01",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "sess_test_001"

    def test_list_sessions(self, app: TestClient, store: SessionStore):
        store.save(_make_session("sess_list_001"))
        resp = app.get("/admin/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        # Cookie values should never appear
        assert "abc" not in resp.text

    def test_session_status(self, app: TestClient, store: SessionStore):
        store.save(_make_session("sess_status_001"))
        resp = app.get("/admin/sessions/sess_status_001/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "sess_status_001"
        assert data["valid"] is True

    def test_invalidate_by_id(self, app: TestClient, store: SessionStore):
        store.save(_make_session("sess_inv_001"))
        resp = app.post("/admin/sessions/invalidate", json={"session_id": "sess_inv_001"})
        assert resp.status_code == 200
        assert not store.exists("sess_inv_001")

    def test_refresh_cookies(self, app: TestClient, store: SessionStore):
        store.save(_make_session("sess_ref_001"))
        resp = app.post(
            "/admin/sessions/sess_ref_001/refresh",
            json={
                "cookies": [{"name": "new_sid", "value": "new_value", "domain": "example.com"}],
            },
        )
        assert resp.status_code == 200
        session = store.load("sess_ref_001")
        assert session.cookies[0].name == "new_sid"
        assert session.cookies[0].value == "new_value"
