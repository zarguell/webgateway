"""Sliding window rate limiter implementation.

Uses in-memory deques of timestamps. Periodic cleanup prunes stale entries.
Thread-safe via asyncio lock since the middleware runs on the async event loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import defaultdict, deque

from serp_llm.config import RateLimitConfig

logger = logging.getLogger(__name__)


class RateLimitExceeded(Exception):
    """Raised when a request exceeds the configured rate limit."""

    def __init__(self, key: str, limit: int, window_seconds: int) -> None:
        self.key = key
        self.limit = limit
        self.window_seconds = window_seconds
        super().__init__(f"Rate limit exceeded for {key}: {limit} per {window_seconds}s")


class SlidingWindowRateLimiter:
    """In-process sliding window rate limiter.

    Tracks request timestamps per key. On each check, drops timestamps
    outside the sliding window, then compares the remaining count against
    the limit.

    Typical keys: ``ip:<client_ip>``, ``key:<api_key_id>``.
    """

    def __init__(self, config: RateLimitConfig) -> None:
        self._config = config
        self._buckets: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()
        self._cleanup_task: asyncio.Task[None] | None = None

    async def start_background_cleanup(self) -> None:
        """Start a background task that periodically prunes stale buckets."""
        if self._cleanup_task is not None:
            return
        interval = self._config.cleanup_interval_seconds
        self._cleanup_task = asyncio.create_task(self._cleanup_loop(interval))

    async def stop_background_cleanup(self) -> None:
        """Cancel the background cleanup task."""
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._cleanup_task
            self._cleanup_task = None

    async def _cleanup_loop(self, interval: int) -> None:
        """Periodically purge buckets with no recent activity."""
        while True:
            await asyncio.sleep(interval)
            await self._prune_stale_buckets()

    async def _prune_stale_buckets(self) -> None:
        """Remove buckets whose newest timestamp is older than the max window."""
        max_window = max(
            self._config.by_key.window_seconds,
            self._config.by_ip.window_seconds,
        )
        now = time.monotonic()
        cutoff = now - max_window * 2  # 2x window for safety margin
        async with self._lock:
            stale = [k for k, dq in self._buckets.items() if not dq or dq[-1] < cutoff]
            for k in stale:
                del self._buckets[k]
            if stale:
                logger.debug("Pruned %d stale rate-limit buckets", len(stale))

    async def check(self, key: str, limit: int, window_seconds: int) -> None:
        """Check whether a request should be allowed.

        Raises ``RateLimitExceeded`` if the limit has been hit.
        Otherwise records the request timestamp and returns.
        """
        now = time.monotonic()
        cutoff = now - window_seconds
        async with self._lock:
            bucket = self._buckets[key]
            # Drop timestamps outside the window
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                raise RateLimitExceeded(key, limit, window_seconds)
            bucket.append(now)

    async def check_multi(
        self,
        keys: list[tuple[str, int, int]],
    ) -> None:
        """Check multiple (key, limit, window) constraints atomically.

        All checks must pass for the request to proceed. If any check fails,
        no timestamps are recorded for any key (all-or-nothing).
        """
        now = time.monotonic()
        async with self._lock:
            # Phase 1: validate all constraints
            for key, limit, window_seconds in keys:
                cutoff = now - window_seconds
                bucket = self._buckets[key]
                while bucket and bucket[0] < cutoff:
                    bucket.popleft()
                if len(bucket) >= limit:
                    raise RateLimitExceeded(key, limit, window_seconds)

            # Phase 2: record all
            for key, _, _ in keys:
                self._buckets[key].append(now)
