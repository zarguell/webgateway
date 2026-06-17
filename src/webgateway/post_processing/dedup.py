from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path


class DedupStore:
    """SHA-256 content deduplication store.

    Tracks content hashes per URL to detect unchanged content on re-fetch.
    Opt-in — off by default (controlled by config.post_processing.deduplication.enabled).
    """

    def __init__(self, db_path: str = "data/dedup.db") -> None:
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS content_hashes (
                url_hash      TEXT PRIMARY KEY,
                content_hash  TEXT NOT NULL,
                ts            REAL NOT NULL
            );
        """)
        self._conn.commit()

    def _url_hash(self, url: str) -> str:
        return hashlib.sha256(url.encode()).hexdigest()

    @staticmethod
    def content_hash(content: str) -> str:
        return hashlib.sha256(content.encode()).hexdigest()

    async def check(self, url: str, content: str) -> tuple[str, bool]:
        """Check if *content* for *url* has changed since last seen.

        Returns:
            (content_hash, content_unchanged).
            content_unchanged is True when the hash matches the stored hash.
            The stored hash is always updated to the current value.
        """
        ch = self.content_hash(content)
        uh = self._url_hash(url)
        now = time.time()

        row = self._conn.execute(
            "SELECT content_hash FROM content_hashes WHERE url_hash = ?",
            (uh,),
        ).fetchone()

        unchanged = row is not None and row[0] == ch

        self._conn.execute(
            "INSERT OR REPLACE INTO content_hashes (url_hash, content_hash, ts) VALUES (?, ?, ?)",
            (uh, ch, now),
        )
        self._conn.commit()

        return ch, unchanged

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
