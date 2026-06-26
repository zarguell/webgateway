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
    await limiter.check("key:test1", 3, 60)
    await limiter.check("key:test1", 3, 60)
    await limiter.check("key:test1", 3, 60)  # exactly at limit


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
    await limiter.check("key:bob", 3, 60)


@pytest.mark.asyncio
async def test_window_slides():
    """After the window passes, old timestamps expire."""
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
    for _ in range(3):
        await limiter.check("key:multi1", 3, 60)

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
async def test_background_cleanup(limiter: SlidingWindowRateLimiter):
    """The cleanup task removes buckets with no recent activity."""
    await limiter.check("key:stale", 3, 60)
    assert "key:stale" in limiter._buckets
    await limiter._prune_stale_buckets()
    assert "key:stale" in limiter._buckets  # still recent, not pruned
