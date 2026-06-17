"""SQLite-backed response cache.

The store is safe to call from async code: every public method is a coroutine
that offloads the blocking SQLite work to the default executor via
``run_in_executor``. A module-level :class:`threading.Lock` serialises every DB
access so a single connection (opened with ``check_same_thread=False``) is
shared safely across the thread pool.

The cache stores opaque JSON strings supplied by the caller; this module never
serialises or deserialises the payload itself.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class CacheStore:
    """Async-friendly SQLite cache.

    The schema and indices are created idempotently on construction. The parent
    directory of ``db_path`` is created if missing.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS cache_entries (
                    cache_key    TEXT PRIMARY KEY,
                    content_type TEXT NOT NULL,
                    data         TEXT NOT NULL,
                    created_ts   REAL NOT NULL,
                    ttl_seconds  INTEGER NOT NULL,
                    provider     TEXT NOT NULL DEFAULT '',
                    url          TEXT NOT NULL DEFAULT '',
                    query        TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_cache_provider ON cache_entries(provider);
                CREATE INDEX IF NOT EXISTS idx_cache_url ON cache_entries(url);
                """
            )
            self._conn.commit()

    # ------------------------------------------------------------------
    # Helpers: run a blocking DB function off the event loop
    # ------------------------------------------------------------------

    async def _run(self, fn: Any) -> Any:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, fn)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get(self, key: str) -> tuple[str, float] | None:
        """Return ``(data, age_seconds)`` for a live entry, else ``None``.

        Expired entries are deleted on read so stale rows are reaped lazily
        without a background sweeper.
        """
        return await self._run(lambda: self._get_sync(key))

    async def set(
        self,
        key: str,
        data: str,
        ttl: int,
        *,
        content_type: str = "",
        provider: str = "",
        url: str = "",
        query: str = "",
    ) -> None:
        """Upsert a cache entry (INSERT OR REPLACE)."""
        await self._run(
            lambda: self._set_sync(
                key, data, ttl, content_type, provider, url, query
            )
        )

    async def invalidate(
        self,
        *,
        url: str | None = None,
        url_pattern: str | None = None,
        provider: str | None = None,
    ) -> int:
        """Delete entries matching *all* supplied criteria and return the count.

        ``url_pattern`` is a shell glob (e.g. ``"*.wsj.com"``) converted to a
        SQL ``LIKE`` pattern matched against the stored ``url`` column.
        """
        return await self._run(
            lambda: self._invalidate_sync(url=url, url_pattern=url_pattern, provider=provider)
        )

    async def flush(self) -> int:
        """Delete every entry. Returns the count removed."""
        return await self._run(self._flush_sync)

    async def stats(self) -> dict[str, Any]:
        """Return ``{"total_entries", "size_bytes", "expired_entries"}``."""
        return await self._run(self._stats_sync)

    # ------------------------------------------------------------------
    # Synchronous DB operations (all guarded by self._lock)
    # ------------------------------------------------------------------

    def _get_sync(self, key: str) -> tuple[str, float] | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT data, created_ts, ttl_seconds FROM cache_entries WHERE cache_key = ?",
                (key,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            data, created_ts, ttl_seconds = row
            age = time.time() - created_ts
            if age >= ttl_seconds:
                self._conn.execute(
                    "DELETE FROM cache_entries WHERE cache_key = ?", (key,)
                )
                self._conn.commit()
                return None
            return data, age

    def _set_sync(
        self,
        key: str,
        data: str,
        ttl: int,
        content_type: str,
        provider: str,
        url: str,
        query: str,
    ) -> None:
        now = time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO cache_entries
                    (cache_key, content_type, data, created_ts, ttl_seconds,
                     provider, url, query)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (key, content_type, data, now, ttl, provider, url, query),
            )
            self._conn.commit()

    def _invalidate_sync(
        self,
        *,
        url: str | None,
        url_pattern: str | None,
        provider: str | None,
    ) -> int:
        clauses: list[str] = []
        params: list[Any] = []
        if url is not None:
            clauses.append("url = ?")
            params.append(url)
        if url_pattern is not None:
            clauses.append("url LIKE ?")
            params.append(_glob_to_like(url_pattern))
        if provider is not None:
            clauses.append("provider = ?")
            params.append(provider)

        where = " AND ".join(clauses) if clauses else "1=1"
        with self._lock:
            cur = self._conn.execute(
                f"SELECT COUNT(*) FROM cache_entries WHERE {where}", params
            )
            count = cur.fetchone()[0]
            self._conn.execute(
                f"DELETE FROM cache_entries WHERE {where}", params
            )
            self._conn.commit()
            return count

    def _flush_sync(self) -> int:
        with self._lock:
            cur = self._conn.execute("SELECT COUNT(*) FROM cache_entries")
            count = cur.fetchone()[0]
            self._conn.execute("DELETE FROM cache_entries")
            self._conn.commit()
            return count

    def _stats_sync(self) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            cur = self._conn.execute("SELECT COUNT(*) FROM cache_entries")
            total = cur.fetchone()[0]
            cur = self._conn.execute(
                "SELECT COUNT(*) FROM cache_entries WHERE ? - created_ts >= ttl_seconds",
                (now,),
            )
            expired = cur.fetchone()[0]
        size_bytes = os.path.getsize(self._db_path)
        return {
            "total_entries": total,
            "size_bytes": size_bytes,
            "expired_entries": expired,
        }


def _glob_to_like(pattern: str) -> str:
    """Convert a shell glob into a SQL ``LIKE`` pattern.

    ``"*.wsj.com"`` becomes ``"%wsj.com%"``: the ``*`` maps to ``%``, a leading
    ``%.`` is collapsed to ``%`` (so the host matches mid-URL), and a trailing
    ``%`` is appended so the pattern matches as a substring of the full URL.
    """
    sql = pattern.replace("*", "%").replace("?", "_")
    if sql.startswith("%."):
        sql = "%" + sql[2:]
    if not sql.endswith("%"):
        sql += "%"
    return sql
