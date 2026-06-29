"""SQLite-backed API key store with bcrypt hashing.

Manages the ``api_keys`` table where operator/admin keys are stored as bcrypt
hashes (plaintext is never persisted). Supports create, list, revoke, and
verify operations. Used by the auth middleware alongside config-based keys.

Key store location is ``data/api_keys.db`` by default.
"""

from __future__ import annotations

import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import bcrypt

__all__ = ["KeyStore", "StoredKey", "KeyNotFound", "KeyRevoked"]

_KEY_ID_PREFIX = "key_"
_SECRET_BYTES = 32  # 43-char base64 token


class KeyNotFound(KeyError):
    """Raised when a key ID is not found in the store."""


class KeyRevoked(ValueError):
    """Raised when trying to use a revoked key."""


@dataclass
class StoredKey:
    """Represents a row from the ``api_keys`` table (no secret hash exposed)."""

    id: str
    label: str
    role: str  # 'operator' | 'admin'
    created_ts: float
    last_used_ts: float | None
    revoked: bool
    revoked_ts: float | None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


class KeyStore:
    """SQLite-backed API key store.

    Args:
        db_path: Path to the SQLite database file (default: ``data/api_keys.db``).

    Thread-safety: Uses one dedicated connection with WAL mode.
    """

    def __init__(self, db_path: str = "data/api_keys.db") -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        """Create the database and table if they don't exist."""
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS api_keys (
                id            TEXT PRIMARY KEY,
                secret_hash   TEXT NOT NULL,
                label         TEXT DEFAULT '',
                role          TEXT NOT NULL DEFAULT 'operator'
                              CHECK(role IN ('operator', 'admin')),
                created_ts    REAL NOT NULL,
                last_used_ts  REAL,
                revoked       INTEGER NOT NULL DEFAULT 0,
                revoked_ts    REAL
            );
        """)
        self._conn.commit()

    def close(self) -> None:
        """Close the SQLite connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def count_keys(self) -> int:
        """Return the total number of keys in the store (including revoked)."""
        assert self._conn is not None
        row = self._conn.execute("SELECT COUNT(*) FROM api_keys").fetchone()
        return row[0] if row else 0

    def count_active_admin_keys(self) -> int:
        """Return the number of non-revoked admin keys."""
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT COUNT(*) FROM api_keys WHERE role = 'admin' AND revoked = 0"
        ).fetchone()
        return row[0] if row else 0

    def list_keys(self) -> list[StoredKey]:
        """Return all keys (metadata only — no hashes)."""
        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT id, label, role, created_ts, last_used_ts, revoked, revoked_ts "
            "FROM api_keys ORDER BY created_ts DESC"
        ).fetchall()
        return [
            StoredKey(
                id=row[0],
                label=row[1] or "",
                role=row[2],
                created_ts=row[3],
                last_used_ts=row[4],
                revoked=bool(row[5]),
                revoked_ts=row[6],
            )
            for row in rows
        ]

    def get_key(self, key_id: str) -> StoredKey:
        """Return metadata for a single key.

        Raises:
            KeyNotFound: If the key ID doesn't exist.
        """
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT id, label, role, created_ts, last_used_ts, revoked, revoked_ts "
            "FROM api_keys WHERE id = ?",
            (key_id,),
        ).fetchone()
        if row is None:
            raise KeyNotFound(key_id)
        return StoredKey(
            id=row[0],
            label=row[1] or "",
            role=row[2],
            created_ts=row[3],
            last_used_ts=row[4],
            revoked=bool(row[5]),
            revoked_ts=row[6],
        )

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def verify_key(self, secret: str) -> StoredKey | None:
        """Look up a key by its plaintext secret.

        Returns the ``StoredKey`` if the secret matches a non-revoked key,
        or ``None`` if no match is found.

        On success, updates ``last_used_ts``.
        """
        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT id, secret_hash, label, role, created_ts, last_used_ts, "
            "revoked, revoked_ts FROM api_keys"
        ).fetchall()
        for row in rows:
            secret_hash = row[1]
            if self._verify_bcrypt(secret, secret_hash):
                key = StoredKey(
                    id=row[0],
                    label=row[2] or "",
                    role=row[3],
                    created_ts=row[4],
                    last_used_ts=row[5],
                    revoked=bool(row[6]),
                    revoked_ts=row[7],
                )
                if key.revoked:
                    return None
                # Update last_used_ts
                self._conn.execute(
                    "UPDATE api_keys SET last_used_ts = ? WHERE id = ?",
                    (time.time(), key.id),
                )
                self._conn.commit()
                return key
        return None

    # ------------------------------------------------------------------
    # Key creation
    # ------------------------------------------------------------------

    def create_key(
        self,
        label: str = "",
        role: str = "operator",
    ) -> tuple[StoredKey, str]:
        """Generate a new API key.

        Returns a ``(StoredKey, plaintext_secret)`` tuple. The plaintext
        secret is shown exactly once — only the bcrypt hash is stored.

        The plaintext secret is a cryptographically random base64 string.
        """
        assert self._conn is not None
        key_id = self._generate_key_id()
        plaintext = self._generate_secret()
        secret_hash = self._hash_bcrypt(plaintext)
        now = time.time()

        self._conn.execute(
            "INSERT INTO api_keys (id, secret_hash, label, role, created_ts) "
            "VALUES (?, ?, ?, ?, ?)",
            (key_id, secret_hash, label, role, now),
        )
        self._conn.commit()

        stored = StoredKey(
            id=key_id,
            label=label,
            role=role,
            created_ts=now,
            last_used_ts=None,
            revoked=False,
            revoked_ts=None,
        )
        return stored, plaintext

    # ------------------------------------------------------------------
    # Revocation
    # ------------------------------------------------------------------

    def revoke_key(self, key_id: str) -> StoredKey:
        """Revoke a key immediately.

        Raises:
            KeyNotFound: If the key ID doesn't exist.
        """
        assert self._conn is not None
        existing = self.get_key(key_id)
        if existing.revoked:
            return existing
        now = time.time()
        self._conn.execute(
            "UPDATE api_keys SET revoked = 1, revoked_ts = ? WHERE id = ?",
            (now, key_id),
        )
        self._conn.commit()
        return StoredKey(
            id=existing.id,
            label=existing.label,
            role=existing.role,
            created_ts=existing.created_ts,
            last_used_ts=existing.last_used_ts,
            revoked=True,
            revoked_ts=now,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_key_id() -> str:
        """Generate a unique human-readable key ID (e.g. ``key_a3F8...``)."""
        suffix = secrets.token_hex(6)  # 12 hex chars
        return f"{_KEY_ID_PREFIX}{suffix}"

    @staticmethod
    def _generate_secret() -> str:
        """Generate a cryptographically random secret string."""
        return secrets.token_urlsafe(_SECRET_BYTES)

    @staticmethod
    def _hash_bcrypt(secret: str) -> str:
        """Hash a plaintext secret with bcrypt."""
        return bcrypt.hashpw(secret.encode("utf-8"), bcrypt.gensalt()).decode(
            "utf-8"
        )

    @staticmethod
    def _verify_bcrypt(secret: str, stored_hash: str) -> bool:
        """Verify a plaintext secret against a bcrypt hash."""
        try:
            return bcrypt.checkpw(
                secret.encode("utf-8"), stored_hash.encode("utf-8")
            )
        except (ValueError, TypeError):
            return False
