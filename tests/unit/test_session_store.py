from __future__ import annotations

import time
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from serp_llm.sessions.models import CookieEntry, SessionData
from serp_llm.sessions.store import SessionNotFound, SessionStore


@pytest.fixture
def key() -> str:
    return Fernet.generate_key().decode()


@pytest.fixture
def store_path(tmp_path: Path) -> str:
    return str(tmp_path / "sessions")


@pytest.fixture
def store(store_path: str, key: str) -> SessionStore:
    return SessionStore(store_path, key)


@pytest.fixture
def sample_session() -> SessionData:
    return SessionData(
        session_id="test_session_001",
        browser_service="invisible_playwright",
        domain="example.com",
        cookies=[
            CookieEntry(name="sessionid", value="abc123", domain="example.com"),
        ],
        user_agent="Mozilla/5.0 Firefox/150.0",
        fingerprint_id="fp_01",
        created_ts=time.time(),
        last_used_ts=time.time(),
        expiry_ts=time.time() + 86400,
    )


class TestSessionStore:
    def test_save_and_load(self, store: SessionStore, sample_session: SessionData):
        store.save(sample_session)
        loaded = store.load(sample_session.session_id)
        assert loaded.session_id == sample_session.session_id
        assert loaded.domain == sample_session.domain
        assert loaded.cookies[0].name == "sessionid"
        assert loaded.cookies[0].value == "abc123"

    def test_load_nonexistent_raises(self, store: SessionStore):
        with pytest.raises(SessionNotFound):
            store.load("nonexistent")

    def test_delete_removes_file(self, store: SessionStore, sample_session: SessionData):
        store.save(sample_session)
        assert store.exists(sample_session.session_id)
        store.delete(sample_session.session_id)
        assert not store.exists(sample_session.session_id)

    def test_delete_missing_is_noop(self, store: SessionStore):
        store.delete("nonexistent")

    def test_list_sessions(self, store: SessionStore, sample_session: SessionData):
        store.save(sample_session)
        sessions = store.list_sessions()
        assert len(sessions) == 1
        info = sessions[0]
        assert info.session_id == "test_session_001"
        assert info.cookie_count == 1
        assert info.browser == "invisible_playwright"

    def test_corrupted_file_raises(self, store: SessionStore, store_path: str):
        enc_path = Path(store_path) / "corrupt.enc"
        enc_path.parent.mkdir(parents=True, exist_ok=True)
        enc_path.write_text("not valid fernet data")
        with pytest.raises(Exception):
            store.load("corrupt")

    def test_wrong_key_raises(self, store_path: str, sample_session: SessionData):
        key1 = Fernet.generate_key().decode()
        store1 = SessionStore(store_path, key1)
        store1.save(sample_session)

        key2 = Fernet.generate_key().decode()
        store2 = SessionStore(store_path, key2)
        with pytest.raises(Exception):
            store2.load(sample_session.session_id)
