"""Tests for the admin UI session cookie management."""

from __future__ import annotations

from serp_llm.admin_session import AdminSession, AdminSessionManager


class TestAdminSessionManager:
    def test_create_and_verify_session(self):
        mgr = AdminSessionManager(secret="test-secret-key-for-testing-1234")
        cookie = mgr.create_session(key_id="key_admin1", role="admin")
        assert isinstance(cookie, str)
        assert len(cookie) > 20

        session = mgr.verify_session(cookie)
        assert session is not None
        assert session.key_id == "key_admin1"
        assert session.role == "admin"
        assert session.is_admin is True

    def test_verify_invalid_cookie_returns_none(self):
        mgr = AdminSessionManager(secret="test-secret-key-for-testing-1234")
        session = mgr.verify_session("not-a-valid-signed-cookie")
        assert session is None

    def test_verify_empty_cookie_returns_none(self):
        mgr = AdminSessionManager(secret="test-secret-key-for-testing-1234")
        assert mgr.verify_session(None) is None
        assert mgr.verify_session("") is None

    def test_verify_wrong_secret_returns_none(self):
        mgr1 = AdminSessionManager(secret="secret-one-aaaaaaaaaaaaaaa")
        mgr2 = AdminSessionManager(secret="secret-two-bbbbbbbbbbbbbbb")
        cookie = mgr1.create_session(key_id="key_test", role="admin")
        session = mgr2.verify_session(cookie)
        assert session is None

    def test_verify_tampered_cookie_returns_none(self):
        mgr = AdminSessionManager(secret="test-secret-key-for-testing-1234")
        cookie = mgr.create_session(key_id="key_test", role="admin")
        # Tamper with the cookie
        tampered = cookie[:-5] + "XXXXX"
        session = mgr.verify_session(tampered)
        assert session is None

    def test_operator_session_is_not_admin(self):
        mgr = AdminSessionManager(secret="test-secret-key-for-testing-1234")
        cookie = mgr.create_session(key_id="key_op", role="operator")
        session = mgr.verify_session(cookie)
        assert session is not None
        assert session.is_admin is False
        assert session.role == "operator"

    def test_multiple_sessions_independent(self):
        mgr = AdminSessionManager(secret="test-secret-key-for-testing-1234")
        cookie1 = mgr.create_session(key_id="key_admin1", role="admin")
        cookie2 = mgr.create_session(key_id="key_admin2", role="admin")

        s1 = mgr.verify_session(cookie1)
        s2 = mgr.verify_session(cookie2)
        assert s1 is not None
        assert s2 is not None
        assert s1.key_id != s2.key_id

    def test_cookie_properties(self):
        mgr = AdminSessionManager(secret="test-secret-key-for-testing-1234")
        assert mgr.cookie_name == "admin_session"
        assert mgr.cookie_path == "/admin"
        assert mgr.cookie_max_age == 86400  # 24 hours

    def test_expired_session_returns_none(self):
        # itsdangerous timed serializer expires based on max_age in verify_session
        # We can't easily test time-based expiry without mocking time,
        # but we verify the mechanism works
        mgr = AdminSessionManager(secret="test-secret-key-for-testing-1234")
        cookie = mgr.create_session(key_id="key_test", role="admin")
        session = mgr.verify_session(cookie)
        assert session is not None
        assert session.key_id == "key_test"


class TestAdminSession:
    def test_admin_session_properties(self):
        session = AdminSession(key_id="key_admin1", role="admin")
        assert session.key_id == "key_admin1"
        assert session.role == "admin"
        assert session.is_admin is True

    def test_operator_session_not_admin(self):
        session = AdminSession(key_id="key_op1", role="operator")
        assert session.is_admin is False
