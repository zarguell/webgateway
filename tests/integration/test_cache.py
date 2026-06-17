"""Integration tests for the response cache (PRD §17).

These tests run against a live Docker Compose stack. They verify:
- Cache hit on repeated search/extract requests
- Cache bypass via request-level cache.read=false
- Admin cache flush / invalidate / stats endpoints
"""

from __future__ import annotations

import time

import httpx
import pytest

GATEWAY_URL = "http://localhost:8080"
AUTH = {"Authorization": "Bearer test-agent-key"}
ADMIN_AUTH = {"Authorization": "Bearer test-admin-key"}


@pytest.fixture(autouse=True)
def _flush_cache():
    httpx.post(f"{GATEWAY_URL}/admin/cache/flush", headers=ADMIN_AUTH, timeout=10)
    yield
    httpx.post(f"{GATEWAY_URL}/admin/cache/flush", headers=ADMIN_AUTH, timeout=10)


class TestSearchCache:
    def test_first_request_is_not_cached(self, client: httpx.Client, auth_headers):
        r = client.post(
            "/search",
            json={"query": "python programming language"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["cached"] is False

    def test_second_request_is_cached(self, client: httpx.Client, auth_headers):
        payload = {"query": "rust programming language", "num_results": 3}
        r1 = client.post("/search", json=payload, headers=auth_headers)
        assert r1.status_code == 200
        assert r1.json()["cached"] is False

        r2 = client.post("/search", json=payload, headers=auth_headers)
        assert r2.status_code == 200
        data2 = r2.json()
        assert data2["cached"] is True
        assert data2["cache_age_seconds"] is not None
        assert data2["cache_age_seconds"] >= 0
        assert data2["results"] == r1.json()["results"]

    def test_cache_bypass_with_read_false(self, client: httpx.Client, auth_headers):
        payload = {
            "query": "golang programming",
            "num_results": 3,
            "cache": {"read": False, "write": True},
        }
        r1 = client.post("/search", json=payload, headers=auth_headers)
        assert r1.status_code == 200
        assert r1.json()["cached"] is False

        r2 = client.post("/search", json=payload, headers=auth_headers)
        assert r2.status_code == 200
        assert r2.json()["cached"] is False

    def test_different_queries_not_cached(self, client: httpx.Client, auth_headers):
        r1 = client.post(
            "/search",
            json={"query": "typescript tutorial"},
            headers=auth_headers,
        )
        r2 = client.post(
            "/search",
            json={"query": "java tutorial"},
            headers=auth_headers,
        )
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json()["cached"] is False
        assert r2.json()["cached"] is False


class TestExtractCache:
    def test_second_extract_is_cached(self, client: httpx.Client, auth_headers):
        time.sleep(0.3)
        payload = {"url": "https://example.com", "format": "markdown"}
        r1 = client.post("/extract", json=payload, headers=auth_headers)
        assert r1.status_code == 200
        assert r1.json()["cached"] is False

        r2 = client.post("/extract", json=payload, headers=auth_headers)
        assert r2.status_code == 200
        data2 = r2.json()
        assert data2["cached"] is True
        assert data2["cache_age_seconds"] is not None
        assert data2["content"] == r1.json()["content"]

    def test_cache_write_false_skips_cache(self, client: httpx.Client, auth_headers):
        time.sleep(0.3)
        payload = {
            "url": "https://example.org",
            "format": "markdown",
            "cache": {"read": False, "write": False},
        }
        r1 = client.post("/extract", json=payload, headers=auth_headers)
        assert r1.status_code == 200
        assert r1.json()["cached"] is False

        payload2 = {
            "url": "https://example.org",
            "format": "markdown",
        }
        r2 = client.post("/extract", json=payload2, headers=auth_headers)
        assert r2.status_code == 200
        assert r2.json()["cached"] is False


class TestCacheAdmin:
    def test_stats_returns_zero_after_flush(self, client: httpx.Client, admin_headers):
        r = client.get("/admin/cache/stats", headers=admin_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["total_entries"] == 0
        assert data["size_bytes"] >= 0

    def test_stats_shows_entries_after_search(
        self, client: httpx.Client, auth_headers, admin_headers
    ):
        client.post(
            "/search",
            json={"query": "cache test query"},
            headers=auth_headers,
        )
        r = client.get("/admin/cache/stats", headers=admin_headers)
        assert r.status_code == 200
        assert r.json()["total_entries"] >= 1

    def test_invalidate_by_provider(self, client: httpx.Client, auth_headers, admin_headers):
        client.post(
            "/search",
            json={"query": "invalidate provider test"},
            headers=auth_headers,
        )
        stats_before = client.get("/admin/cache/stats", headers=admin_headers).json()
        assert stats_before["total_entries"] >= 1

        r = client.post(
            "/admin/cache/invalidate",
            json={"provider": "searxng"},
            headers=admin_headers,
        )
        assert r.status_code == 200
        assert r.json()["invalidated"] >= 1

        stats_after = client.get("/admin/cache/stats", headers=admin_headers).json()
        assert stats_after["total_entries"] < stats_before["total_entries"]

    def test_flush_clears_all(self, client: httpx.Client, auth_headers, admin_headers):
        client.post(
            "/search",
            json={"query": "flush test"},
            headers=auth_headers,
        )
        assert client.get("/admin/cache/stats", headers=admin_headers).json()["total_entries"] >= 1

        r = client.post("/admin/cache/flush", headers=admin_headers)
        assert r.status_code == 200
        assert r.json()["flushed"] >= 1

        stats = client.get("/admin/cache/stats", headers=admin_headers).json()
        assert stats["total_entries"] == 0

    def test_cache_admin_requires_admin(self, client: httpx.Client, auth_headers):
        r = client.get("/admin/cache/stats", headers=auth_headers)
        assert r.status_code == 403
