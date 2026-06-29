# Rate Limiting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-key and per-IP sliding window rate limiting to the FastAPI gateway to prevent API key abuse and request flooding.

**Architecture:** In-process sliding window counter middleware. Each (key_or_ip, window) tuple maintains a deque of timestamps. A background cleanup task prunes expired windows periodically. No external dependency (Redis, etc.) — keeps the self-hosted ethos. Configurable via `config.yaml` under a new `rate_limiting` section.

**Tech Stack:** Python `collections.deque`, `time.monotonic`, FastAPI middleware pattern, Pydantic config model.

---

### Task 1: Rate limiter config models

**Files:**
- Modify: `src/webgateway/config.py` (add config models after `CacheConfig`)
- Test: `tests/unit/test_config.py` (new test class)

- [ ] **Step 1: Add RateLimitConfig models to config.py**

Insert after the `CacheConfig` class (line 262) in `src/webgateway/config.py`:

```python
class RateLimitByKey(BaseModel):
    """Per-key rate limit configuration."""
    requests: int = 60
    window_seconds: int = 60


class RateLimitByIP(BaseModel):
    """Per-IP rate limit configuration."""
    requests: int = 30
    window_seconds: int = 60


class RateLimitConfig(BaseModel):
    """Sliding window rate limiting configuration."""
    enabled: bool = False
    by_key: RateLimitByKey = Field(default_factory=RateLimitByKey)
    by_ip: RateLimitByIP = Field(default_factory=RateLimitByIP)
    cleanup_interval_seconds: int = 300
```

Add the field to `GatewayConfig` (around line 445, after `alerts`):

```python
    alerts: AlertConfig = Field(default_factory=AlertConfig)
    rate_limiting: RateLimitConfig = Field(default_factory=RateLimitConfig)  # <-- add this
    mcp: MCPConfig = Field(default_factory=MCPConfig)
```

- [ ] **Step 2: Write config test**

Create `tests/unit/test_config.py`:

```python
"""Tests for configuration models — rate limiting section."""

from webgateway.config import GatewayConfig, RateLimitConfig


def test_rate_limit_config_defaults():
    """Default rate limit config has sensible values and is disabled."""
    config = GatewayConfig()
    rl = config.rate_limiting
    assert rl.enabled is False
    assert rl.by_key.requests == 60
    assert rl.by_key.window_seconds == 60
    assert rl.by_ip.requests == 30
    assert rl.by_ip.window_seconds == 60
    assert rl.cleanup_interval_seconds == 300


def test_rate_limit_config_custom():
    """Custom values override defaults."""
    config = GatewayConfig.model_validate({
        "rate_limiting": {
            "enabled": True,
            "by_key": {"requests": 100, "window_seconds": 120},
        }
    })
    rl = config.rate_limiting
    assert rl.enabled is True
    assert rl.by_key.requests == 100
    assert rl.by_key.window_seconds == 120
    assert rl.by_ip.requests == 30  # unchanged default
```

- [ ] **Step 3: Run config tests**

```bash
source .venv/bin/activate && pytest tests/unit/test_config.py -v
```

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/webgateway/config.py tests/unit/test_config.py
git commit -m "feat(config): add rate limiting config models"
```

---

### Task 2: Rate limiter implementation

**Files:**
- Create: `src/webgateway/ratelimit/__init__.py`
- Create: `src/webgateway/ratelimit/limiter.py`

- [ ] **Step 1: Create ratelimit package**

```bash
mkdir -p src/webgateway/ratelimit
```

Create `src/webgateway/ratelimit/__init__.py`:

```python
"""Sliding window rate limiter with per-key and per-IP tracking."""
```

Create `src/webgateway/ratelimit/limiter.py`:

```python
"""Sliding window rate limiter implementation.

Uses in-memory deques of timestamps. Periodic cleanup prunes stale entries.
Thread-safe via asyncio lock since the middleware runs on the async event loop.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque

from webgateway.config import RateLimitConfig

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
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
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
```

- [ ] **Step 2: Commit**

```bash
git add src/webgateway/ratelimit/
git commit -m "feat(ratelimit): sliding window rate limiter implementation"
```

---

### Task 3: Rate limiter unit tests

**Files:**
- Create: `tests/unit/test_ratelimit.py`

- [ ] **Step 1: Write rate limiter tests**

Create `tests/unit/test_ratelimit.py`:

```python
"""Tests for the sliding window rate limiter."""

import asyncio

import pytest

from webgateway.config import RateLimitConfig
from webgateway.ratelimit.limiter import RateLimitExceeded, SlidingWindowRateLimiter


@pytest.fixture()
def config() -> RateLimitConfig:
    return RateLimitConfig(
        enabled=True,
        by_key={"requests": 3, "window_seconds": 60},
        by_ip={"requests": 5, "window_seconds": 60},
    )


@pytest.fixture()
def limiter(config: RateLimitConfig) -> SlidingWindowRateLimiter:
    return SlidingWindowRateLimiter(config)


@pytest.mark.asyncio
async def test_allows_requests_under_limit(limiter: SlidingWindowRateLimiter):
    """Requests within the limit pass through."""
    await limiter.check("key:test1", 3, 60)  # ok
    await limiter.check("key:test1", 3, 60)  # ok
    await limiter.check("key:test1", 3, 60)  # ok — exactly at limit


@pytest.mark.asyncio
async def test_blocks_requests_over_limit(limiter: SlidingWindowRateLimiter):
    """The (limit+1)th request is blocked."""
    for _ in range(3):
        await limiter.check("key:test2", 3, 60)
    with pytest.raises(RateLimitExceeded) as exc:
        await limiter.check("key:test2", 3, 60)
    assert "test2" in str(exc.value)


@pytest.mark.asyncio
async def test_different_keys_are_independent(limiter: SlidingWindowRateLimiter):
    """Two different keys have separate counters."""
    for _ in range(3):
        await limiter.check("key:alice", 3, 60)
    # Bob should still be allowed
    await limiter.check("key:bob", 3, 60)


@pytest.mark.asyncio
async def test_window_slides(limiter: SlidingWindowRateLimiter):
    """After the window passes, old timestamps expire and new requests are allowed."""
    small_limiter = SlidingWindowRateLimiter(
        RateLimitConfig(by_key={"requests": 1, "window_seconds": 1})
    )
    await small_limiter.check("key:slide", 1, 1)
    with pytest.raises(RateLimitExceeded):
        await small_limiter.check("key:slide", 1, 1)
    await asyncio.sleep(1.1)
    # Window has slid — old timestamp expired
    await small_limiter.check("key:slide", 1, 1)


@pytest.mark.asyncio
async def test_check_multi_all_or_nothing(limiter: SlidingWindowRateLimiter):
    """check_multi either records all keys or none."""
    # Fill up key1 to the limit
    for _ in range(3):
        await limiter.check("key:multi1", 3, 60)

    # key2 still has capacity, but key1 is full → check_multi should reject both
    with pytest.raises(RateLimitExceeded):
        await limiter.check_multi([
            ("key:multi1", 3, 60),
            ("key:multi2", 3, 60),
        ])

    # key2 should not have been recorded
    await limiter.check("key:multi2", 3, 60)
    await limiter.check("key:multi2", 3, 60)
    await limiter.check("key:multi2", 3, 60)


@pytest.mark.asyncio
async def test_background_cleanup_prunes_stale_buckets(limiter: SlidingWindowRateLimiter):
    """The cleanup task removes buckets with no recent activity."""
    await limiter.check("key:stale", 3, 60)
    assert "key:stale" in limiter._buckets
    await limiter._prune_stale_buckets()  # Manual trigger
    # Bucket should not be pruned yet — it's recent
    assert "key:stale" in limiter._buckets
```

- [ ] **Step 2: Run tests**

```bash
source .venv/bin/activate && pytest tests/unit/test_ratelimit.py -v
```

Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_ratelimit.py
git commit -m "test(ratelimit): unit tests for sliding window rate limiter"
```

---

### Task 4: Rate limiting middleware

**Files:**
- Create: `src/webgateway/ratelimit/middleware.py`
- Modify: `src/webgateway/main.py` (wire middleware)
- Modify: `docs-src/docs/configuration/config-yaml.md` (document new section)

- [ ] **Step 1: Write the rate limiting middleware**

Create `src/webgateway/ratelimit/middleware.py`:

```python
"""FastAPI middleware that enforces per-key and per-IP rate limits."""

from __future__ import annotations

import logging

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from webgateway.config import ConfigManager
from webgateway.ratelimit.limiter import RateLimitExceeded, SlidingWindowRateLimiter

logger = logging.getLogger(__name__)


_RATE_LIMITED_PATHS = ("/search", "/extract", "/mcp")


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window rate limiting for search/extract endpoints.

    Enforces two limits per request (when both are configured):
    1. Per-API-key limit (by ``api_key_id`` on request state)
    2. Per-IP limit (by ``request.client.host``)

    Returns HTTP 429 when a limit is exceeded.
    """

    def __init__(self, app, config_manager: ConfigManager) -> None:
        super().__init__(app)
        self._limiter = SlidingWindowRateLimiter(config_manager.config.rate_limiting)
        self._config_manager = config_manager

    async def start_cleanup(self) -> None:
        await self._limiter.start_background_cleanup()

    async def stop_cleanup(self) -> None:
        await self._limiter.stop_background_cleanup()

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        config = self._config_manager.config.rate_limiting
        if not config.enabled:
            return await call_next(request)

        # Only rate-limit the data endpoints, not health/admin/docs
        if not any(request.url.path.startswith(p) for p in _RATE_LIMITED_PATHS):
            return await call_next(request)

        checks: list[tuple[str, int, int]] = []

        # Per-IP limit
        client_host = request.client.host if request.client else "unknown"
        checks.append((
            f"ip:{client_host}",
            config.by_ip.requests,
            config.by_ip.window_seconds,
        ))

        # Per-key limit (if authenticated)
        api_key_id: str | None = getattr(request.state, "api_key_id", None)
        if api_key_id:
            checks.append((
                f"key:{api_key_id}",
                config.by_key.requests,
                config.by_key.window_seconds,
            ))

        try:
            await self._limiter.check_multi(checks)
        except RateLimitExceeded as exc:
            logger.warning("Rate limit exceeded: %s", exc)
            return Response(
                status_code=429,
                content=(
                    f'{{"error":"rate_limit_exceeded",'
                    f'"detail":"{exc}",'
                    f'"retry_after":{exc.window_seconds}}}'
                ),
                media_type="application/json",
                headers={"Retry-After": str(exc.window_seconds)},
            )

        return await call_next(request)
```

- [ ] **Step 2: Wire the middleware in main.py**

In `src/webgateway/main.py`, add an import after the other middleware imports:

```python
from webgateway.ratelimit.limiter import SlidingWindowRateLimiter
from webgateway.ratelimit.middleware import RateLimitMiddleware
```

Inside the `lifespan()` function, after creating `config_manager` (around line 70) AND after the rate limiter middleware has been added to the app in `create_app()`, initialize the limiter and start its cleanup:

Add to `lifespan()` — create the rate limiter and store on app.state:

```python
    config_manager = ConfigManager(config_path)
    app.state.config_manager = config_manager

    # --- Rate limiting (background bucket cleanup) ---
    rate_limiter = SlidingWindowRateLimiter(config_manager.config.rate_limiting)
    app.state.rate_limiter = rate_limiter
    await rate_limiter.start_background_cleanup()
```

Add cleanup after `yield`. Modify the yield section to:

```python
    if mcp_ctx:
        async with mcp_ctx:
            yield
    else:
        yield

    # Cleanup rate limiter background task
    if hasattr(app.state, "rate_limiter"):
        await app.state.rate_limiter.stop_background_cleanup()
```

In `create_app()`, add the middleware after route inclusion (around line 232):

```python
    # --- Rate limiting middleware ---
    app.add_middleware(RateLimitMiddleware)
```

The middleware reads its config from `app.state.rate_limiter` at runtime, so it doesn't need the config_manager at construction time.

Update the middleware to read from `app.state` rather than requiring a config_manager in `__init__`. In `src/webgateway/ratelimit/middleware.py`, replace the class with:

```python
class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window rate limiting for search/extract endpoints.

    Reads the ``SlidingWindowRateLimiter`` instance from ``app.state.rate_limiter``,
    which is initialized during the application lifespan.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        limiter: SlidingWindowRateLimiter | None = getattr(
            request.app.state, "rate_limiter", None
        )
        if limiter is None:
            return await call_next(request)

        config = request.app.state.config_manager.config.rate_limiting
        if not config.enabled:
            return await call_next(request)

        # Only rate-limit the data endpoints, not health/admin/docs
        if not any(request.url.path.startswith(p) for p in _RATE_LIMITED_PATHS):
            return await call_next(request)

        checks: list[tuple[str, int, int]] = []

        # Per-IP limit
        client_host = request.client.host if request.client else "unknown"
        checks.append((
            f"ip:{client_host}",
            config.by_ip.requests,
            config.by_ip.window_seconds,
        ))

        # Per-key limit (if authenticated)
        api_key_id: str | None = getattr(request.state, "api_key_id", None)
        if api_key_id:
            checks.append((
                f"key:{api_key_id}",
                config.by_key.requests,
                config.by_key.window_seconds,
            ))

        try:
            await limiter.check_multi(checks)
        except RateLimitExceeded as exc:
            logger.warning("Rate limit exceeded: %s", exc)
            return Response(
                status_code=429,
                content=(
                    f'{{"error":"rate_limit_exceeded",'
                    f'"detail":"{exc}",'
                    f'"retry_after":{exc.window_seconds}}}'
                ),
                media_type="application/json",
                headers={"Retry-After": str(exc.window_seconds)},
            )

        return await call_next(request)
```

Remove the `__init__` method from `RateLimitMiddleware` (the class inherits `BaseHTTPMiddleware.__init__` which only takes `app`).

- [ ] **Step 3: Update config-yaml.md documentation**

Read the current file first, then append a rate limiting section at the end of `docs-src/docs/configuration/config-yaml.md`:

```markdown
### rate_limiting

Sliding window rate limiting for search and extract endpoints.

```yaml
rate_limiting:
  enabled: true
  by_key:
    requests: 60
    window_seconds: 60
  by_ip:
    requests: 30
    window_seconds: 60
  cleanup_interval_seconds: 300
```

- `enabled`: Set to `true` to activate rate limiting (default: `false`).
- `by_key.requests`: Max requests per API key in the sliding window.
- `by_key.window_seconds`: Width of the sliding window in seconds.
- `by_ip.requests`: Max requests per client IP in the sliding window.
- `cleanup_interval_seconds`: How often stale tracking buckets are pruned.
```

- [ ] **Step 4: Commit**

```bash
git add src/webgateway/ratelimit/middleware.py src/webgateway/main.py docs-src/docs/configuration/config-yaml.md
git commit -m "feat(ratelimit): middleware wiring and config docs"
```

---

### Task 5: Integration test for rate limiting

**Files:**
- Create: `tests/integration/test_rate_limiting.py`

- [ ] **Step 1: Write integration test**

Create `tests/integration/test_rate_limiting.py`:

```python
"""Integration tests for rate limiting.

Requires rate limiting to be enabled in the test gateway config.
These tests hit the live Docker Compose stack.
"""

from __future__ import annotations

import httpx
import pytest

GATEWAY_URL = "http://localhost:8080"


@pytest.fixture(scope="module")
def client() -> httpx.Client:
    return httpx.Client(base_url=GATEWAY_URL, timeout=30)


def test_rate_limit_returns_429(client: httpx.Client, auth_headers: dict[str, str]):
    """Sending requests rapidly should eventually trigger a 429."""
    payload = {"query": "test rate limiting", "num_results": 1}
    statuses: list[int] = []
    for _ in range(20):
        r = client.post("/search", json=payload, headers=auth_headers)
        statuses.append(r.status_code)
        if r.status_code == 429:
            break

    # At least one request should have been rate-limited
    assert 429 in statuses, (
        f"Expected at least one 429 among: {statuses}. "
        "Is rate limiting enabled in config.test.yaml?"
    )
    body = r.json()
    assert "detail" in body


def test_rate_limit_retry_after_header(
    client: httpx.Client, auth_headers: dict[str, str]
):
    """A 429 response should include a Retry-After header."""
    payload = {"query": "test retry after", "num_results": 1}
    for _ in range(30):
        r = client.post("/search", json=payload, headers=auth_headers)
        if r.status_code == 429:
            assert "retry-after" in r.headers
            break
```

- [ ] **Step 2: Enable rate limiting in test config**

Add to `config.test.yaml` (find the right spot — after `alerts:` or similar):

```yaml
rate_limiting:
  enabled: true
  by_key:
    requests: 5
    window_seconds: 10
  by_ip:
    requests: 10
    window_seconds: 60
```

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_rate_limiting.py config.test.yaml
git commit -m "test(ratelimit): integration test and test config"
```
