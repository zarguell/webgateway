"""Tests for the SQLite-backed API key store."""

from __future__ import annotations

import tempfile
from pathlib import Path

import bcrypt
import pytest

from webgateway.key_store import KeyNotFound, KeyStore, StoredKey


@pytest.fixture
def store() -> KeyStore:
    """Create a KeyStore backed by a temp file for each test."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    ks = KeyStore(db_path=db_path)
    yield ks
    ks.close()
    Path(db_path).unlink(missing_ok=True)


class TestKeyStore:
    def test_create_operator_key(self, store: KeyStore):
        stored, plaintext = store.create_key(label="test-operator", role="operator")
        assert stored.id.startswith("key_")
        assert stored.label == "test-operator"
        assert stored.role == "operator"
        assert stored.revoked is False
        assert isinstance(plaintext, str)
        assert len(plaintext) > 20

    def test_create_admin_key(self, store: KeyStore):
        stored, plaintext = store.create_key(label="test-admin", role="admin")
        assert stored.role == "admin"
        assert stored.is_admin is True
        assert isinstance(plaintext, str)

    def test_list_keys_returns_created_keys(self, store: KeyStore):
        store.create_key(label="key1", role="operator")
        store.create_key(label="key2", role="admin")
        keys = store.list_keys()
        assert len(keys) == 2
        labels = {k.label for k in keys}
        assert labels == {"key1", "key2"}

    def test_list_keys_never_exposes_secret_hash(self, store: KeyStore):
        store.create_key(label="secret-test", role="operator")
        keys = store.list_keys()
        for k in keys:
            assert not hasattr(k, "secret_hash")

    def test_get_key_by_id(self, store: KeyStore):
        stored, _ = store.create_key(label="find-me", role="operator")
        found = store.get_key(stored.id)
        assert found.id == stored.id
        assert found.label == "find-me"

    def test_get_key_not_found_raises(self, store: KeyStore):
        with pytest.raises(KeyNotFound):
            store.get_key("key_nonexistent")

    def test_verify_key_valid(self, store: KeyStore):
        stored, plaintext = store.create_key(label="verify-test", role="operator")
        result = store.verify_key(plaintext)
        assert result is not None
        assert result.id == stored.id
        assert result.revoked is False

    def test_verify_key_revoked_returns_none(self, store: KeyStore):
        stored, plaintext = store.create_key(label="revoke-test", role="operator")
        store.revoke_key(stored.id)
        result = store.verify_key(plaintext)
        assert result is None

    def test_verify_key_wrong_secret_returns_none(self, store: KeyStore):
        store.create_key(label="wrong", role="operator")
        result = store.verify_key("wrong-secret-that-does-not-exist")
        assert result is None

    def test_revoke_key(self, store: KeyStore):
        stored, _ = store.create_key(label="to-revoke", role="admin")
        revoked = store.revoke_key(stored.id)
        assert revoked.revoked is True
        assert revoked.revoked_ts is not None
        # Verify it shows as revoked in list
        keys = store.list_keys()
        matching = [k for k in keys if k.id == stored.id]
        assert len(matching) == 1
        assert matching[0].revoked is True

    def test_revoke_already_revoked_is_noop(self, store: KeyStore):
        stored, _ = store.create_key(label="double-revoke", role="operator")
        store.revoke_key(stored.id)
        result = store.revoke_key(stored.id)
        assert result.revoked is True

    def test_count_active_admin_keys(self, store: KeyStore):
        assert store.count_active_admin_keys() == 0
        store.create_key(label="admin1", role="admin")
        assert store.count_active_admin_keys() == 1
        store.create_key(label="admin2", role="admin")
        assert store.count_active_admin_keys() == 2
        store.create_key(label="op1", role="operator")
        assert store.count_active_admin_keys() == 2

    def test_count_keys(self, store: KeyStore):
        assert store.count_keys() == 0
        store.create_key(label="k1", role="operator")
        assert store.count_keys() == 1
        store.create_key(label="k2", role="admin")
        assert store.count_keys() == 2

    def test_stored_key_is_admin_property(self):
        admin_key = StoredKey(
            id="key_admin", label="", role="admin",
            created_ts=0.0, last_used_ts=None, revoked=False, revoked_ts=None,
        )
        assert admin_key.is_admin is True
        op_key = StoredKey(
            id="key_op", label="", role="operator",
            created_ts=0.0, last_used_ts=None, revoked=False, revoked_ts=None,
        )
        assert op_key.is_admin is False

    def test_bcrypt_hash_format(self, store: KeyStore):
        _, plaintext = store.create_key(label="bcrypt-test", role="operator")
        # Verify the stored hash is a valid bcrypt hash
        import sqlite3
        conn = sqlite3.connect(store._db_path)
        row = conn.execute(
            "SELECT secret_hash FROM api_keys WHERE label = ?",
            ("bcrypt-test",),
        ).fetchone()
        conn.close()
        assert row is not None
        stored_hash = row[0]
        assert stored_hash.startswith("$2b$") or stored_hash.startswith("$2a$")
        assert bcrypt.checkpw(plaintext.encode(), stored_hash.encode())

    def test_verify_key_updates_last_used(self, store: KeyStore):
        stored, plaintext = store.create_key(label="last-used", role="operator")
        assert stored.last_used_ts is None
        store.verify_key(plaintext)
        found = store.get_key(stored.id)
        assert found.last_used_ts is not None
