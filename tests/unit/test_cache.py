"""Unit tests for the cache package: store, keys, TTL resolution, quality validator."""

from __future__ import annotations

import asyncio
import time

import pytest

from webgateway.cache.keys import extract_key, search_key
from webgateway.cache.quality import validate_content
from webgateway.cache.store import CacheStore
from webgateway.cache.ttl import resolve_ttl
from webgateway.config import CacheMatch, CacheTTLRule

# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------


class TestKeys:
    def test_search_key_is_deterministic(self):
        k1 = search_key("python", "searxng", 10)
        k2 = search_key("python", "searxng", 10)
        assert k1 == k2

    def test_search_key_changes_with_query(self):
        assert search_key("python", "searxng", 10) != search_key("rust", "searxng", 10)

    def test_search_key_changes_with_provider(self):
        assert search_key("python", "searxng", 10) != search_key("python", "brave", 10)

    def test_search_key_changes_with_num_results(self):
        assert search_key("python", "searxng", 10) != search_key("python", "searxng", 5)

    def test_extract_key_is_deterministic(self):
        k1 = extract_key("https://example.com", "markdown", None, "jina")
        k2 = extract_key("https://example.com", "markdown", None, "jina")
        assert k1 == k2

    def test_extract_key_changes_with_url(self):
        assert extract_key("https://a.com", "markdown", None, "jina") != extract_key(
            "https://b.com", "markdown", None, "jina"
        )

    def test_extract_key_changes_with_format(self):
        assert extract_key("https://example.com", "markdown", None, "jina") != extract_key(
            "https://example.com", "html", None, "jina"
        )

    def test_extract_key_changes_with_session(self):
        assert extract_key("https://example.com", "markdown", None, "jina") != extract_key(
            "https://example.com", "markdown", "profile1", "jina"
        )

    def test_extract_key_changes_with_provider(self):
        assert extract_key("https://example.com", "markdown", None, "jina") != extract_key(
            "https://example.com", "markdown", None, "firecrawl"
        )


# ---------------------------------------------------------------------------
# TTL resolution
# ---------------------------------------------------------------------------


class TestTTLResolution:
    def test_default_ttl_when_no_rules(self):
        assert resolve_ttl([], 300) == 300

    def test_provider_match(self):
        rules = [
            CacheTTLRule(
                match=CacheMatch(provider=["brave", "tavily"]),
                ttl=120,
            ),
        ]
        assert resolve_ttl(rules, 300, provider="brave") == 120
        assert resolve_ttl(rules, 300, provider="tavily") == 120
        assert resolve_ttl(rules, 300, provider="searxng") == 300

    def test_domain_glob_match(self):
        rules = [
            CacheTTLRule(
                match=CacheMatch(domain_glob=["*.wikipedia.org"]),
                ttl=86400,
            ),
        ]
        assert resolve_ttl(rules, 300, url="https://en.wikipedia.org/wiki/Python") == 86400
        assert resolve_ttl(rules, 300, url="https://example.com") == 300

    def test_content_type_match(self):
        rules = [
            CacheTTLRule(
                match=CacheMatch(content_type="search"),
                ttl=600,
            ),
        ]
        assert resolve_ttl(rules, 300, content_type="search") == 600
        assert resolve_ttl(rules, 300, content_type="extract") == 300

    def test_url_pattern_match(self):
        rules = [
            CacheTTLRule(
                match=CacheMatch(url_pattern=r".*/(about|pricing).*"),
                ttl=3600,
            ),
        ]
        assert resolve_ttl(rules, 300, url="https://example.com/about") == 3600
        assert resolve_ttl(rules, 300, url="https://example.com/blog") == 300

    def test_first_match_wins(self):
        rules = [
            CacheTTLRule(match=CacheMatch(provider=["brave"]), ttl=100),
            CacheTTLRule(match=CacheMatch(provider=["brave"]), ttl=200),
        ]
        assert resolve_ttl(rules, 300, provider="brave") == 100

    def test_multiple_criteria_all_must_match(self):
        rules = [
            CacheTTLRule(
                match=CacheMatch(content_type="search", provider=["searxng"]),
                ttl=600,
            ),
        ]
        assert resolve_ttl(rules, 300, content_type="search", provider="searxng") == 600
        assert resolve_ttl(rules, 300, content_type="search", provider="brave") == 300
        assert resolve_ttl(rules, 300, content_type="extract", provider="searxng") == 300


# ---------------------------------------------------------------------------
# Quality validator
# ---------------------------------------------------------------------------


class TestQualityValidator:
    def test_empty_triggers_always_pass(self):
        passed, reason = validate_content("hello world", [])
        assert passed is True
        assert reason is None

    def test_content_length_pass(self):
        triggers = [{"condition": {"content_length_bytes": 100}, "action": "invalidate"}]
        passed, reason = validate_content("x" * 200, triggers)
        assert passed is True

    def test_content_length_fail(self):
        triggers = [{"condition": {"content_length_bytes": 500}, "action": "invalidate"}]
        passed, reason = validate_content("short", triggers)
        assert passed is False
        assert "content_length_bytes" in reason

    def test_content_contains_pass(self):
        triggers = [
            {
                "condition": {"content_contains": ["<script>", "Are you a robot"]},
                "action": "invalidate",
            }
        ]
        passed, reason = validate_content("clean content here", triggers)
        assert passed is True

    def test_content_contains_fail(self):
        triggers = [
            {
                "condition": {"content_contains": ["<script>", "Are you a robot"]},
                "action": "invalidate",
            }
        ]
        passed, reason = validate_content("page says: Are you a robot?", triggers)
        assert passed is False
        assert "Are you a robot" in reason

    def test_provider_error_class_skipped(self):
        triggers = [
            {"condition": {"provider_error_class": [403, 429]}, "action": "invalidate"},
        ]
        passed, reason = validate_content("any content", triggers)
        assert passed is True

    def test_first_failure_wins(self):
        triggers = [
            {"condition": {"content_length_bytes": 1000}, "action": "invalidate"},
            {"condition": {"content_contains": ["bad"]}, "action": "invalidate"},
        ]
        passed, reason = validate_content("bad", triggers)
        assert passed is False
        assert "content_length_bytes" in reason


# ---------------------------------------------------------------------------
# CacheStore
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path):
    return CacheStore(str(tmp_path / "test_cache.db"))


class TestCacheStore:
    def test_set_and_get(self, store):
        asyncio.run(store.set("k1", '{"data": 1}', 300, content_type="search"))

        result = asyncio.run(store.get("k1"))
        assert result is not None
        data, age = result
        assert data == '{"data": 1}'
        assert age >= 0

    def test_get_missing_returns_none(self, store):
        assert asyncio.run(store.get("nonexistent")) is None

    def test_expired_entry_returns_none(self, store):
        asyncio.run(store.set("k1", "data", 0))
        time.sleep(0.05)
        assert asyncio.run(store.get("k1")) is None

    def test_upsert_replaces_existing(self, store):
        asyncio.run(store.set("k1", "old", 300))
        asyncio.run(store.set("k1", "new", 300))
        result = asyncio.run(store.get("k1"))
        assert result is not None
        assert result[0] == "new"

    def test_invalidate_by_provider(self, store):
        asyncio.run(store.set("k1", "d1", 300, provider="brave"))
        asyncio.run(store.set("k2", "d2", 300, provider="searxng"))
        count = asyncio.run(store.invalidate(provider="brave"))
        assert count == 1
        assert asyncio.run(store.get("k1")) is None
        assert asyncio.run(store.get("k2")) is not None

    def test_invalidate_by_url(self, store):
        asyncio.run(store.set("k1", "d1", 300, url="https://example.com/page"))
        asyncio.run(store.set("k2", "d2", 300, url="https://other.com"))
        count = asyncio.run(store.invalidate(url="https://example.com/page"))
        assert count == 1
        assert asyncio.run(store.get("k1")) is None

    def test_invalidate_by_url_pattern(self, store):
        asyncio.run(store.set("k1", "d1", 300, url="https://www.wsj.com/article1"))
        asyncio.run(store.set("k2", "d2", 300, url="https://example.com"))
        count = asyncio.run(store.invalidate(url_pattern="*.wsj.com"))
        assert count == 1
        assert asyncio.run(store.get("k1")) is None
        assert asyncio.run(store.get("k2")) is not None

    def test_flush(self, store):
        asyncio.run(store.set("k1", "d1", 300))
        asyncio.run(store.set("k2", "d2", 300))
        count = asyncio.run(store.flush())
        assert count == 2
        assert asyncio.run(store.get("k1")) is None

    def test_stats(self, store):
        asyncio.run(store.set("k1", "d1", 300))
        asyncio.run(store.set("k2", "d2", 300))
        stats = asyncio.run(store.stats())
        assert stats["total_entries"] == 2
        assert stats["size_bytes"] > 0
        assert stats["expired_entries"] == 0

    def test_stats_counts_expired(self, store):
        asyncio.run(store.set("k1", "d1", 0))
        time.sleep(0.05)
        asyncio.run(store.set("k2", "d2", 300))
        stats = asyncio.run(store.stats())
        assert stats["total_entries"] == 2
        assert stats["expired_entries"] == 1
