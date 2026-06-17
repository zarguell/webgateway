"""Integration tests for the invisible_playwright sidecar REST API.

These tests hit the sidecar directly at ``INVISIBLE_PLAYWRIGHT_URL``
(default ``http://localhost:3001``) to verify the ``POST /scrape`` and
``GET /health`` endpoints work.

The tests auto-skip when the sidecar isn't running.  Start it with::

    docker compose -f docker-compose.test.yml \\
        -f docker-compose.invisible-playwright.yml \\
        up -d --build
"""

from __future__ import annotations

import httpx
import pytest

EXAMPLE_COM_URL = "https://example.com"
EXAMPLE_COM_TITLE = "Example Domain"


class TestHealth:
    def test_health_returns_ok(
        self, invisible_playwright_available: None, ipw_client: httpx.Client
    ):
        resp = ipw_client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestScrape:
    def test_scrape_basic_page(
        self, invisible_playwright_available: None, ipw_client: httpx.Client
    ):
        """Scrape example.com and verify we get content back."""
        resp = ipw_client.post(
            "/scrape",
            json={"url": EXAMPLE_COM_URL, "timeout": 30000},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["url"] == EXAMPLE_COM_URL
        assert len(data["content"]) > 0
        assert EXAMPLE_COM_TITLE in data.get("title", "")

    def test_scrape_response_schema(
        self, invisible_playwright_available: None, ipw_client: httpx.Client
    ):
        """Verify all required fields are present."""
        resp = ipw_client.post(
            "/scrape",
            json={"url": EXAMPLE_COM_URL},
        )
        assert resp.status_code == 200
        data = resp.json()
        for field in ("content", "format", "url", "title"):
            assert field in data, f"Missing required field: {field}"
        assert data["format"] == "markdown"

    def test_scrape_with_wait_selector(
        self, invisible_playwright_available: None, ipw_client: httpx.Client
    ):
        """wait_for_selector is accepted without error even if not found."""
        resp = ipw_client.post(
            "/scrape",
            json={
                "url": EXAMPLE_COM_URL,
                "wait_for_selector": "h1",
                "timeout": 15000,
            },
        )
        assert resp.status_code == 200
        assert len(resp.json()["content"]) > 0

    def test_scrape_invalid_url_returns_error(
        self, invisible_playwright_available: None, ipw_client: httpx.Client
    ):
        resp = ipw_client.post(
            "/scrape",
            json={"url": "not-a-valid-url"},
        )
        # Should fail with 502 (bad gateway / upstream error)
        assert resp.status_code == 502

    def test_scrape_with_proxy(
        self, invisible_playwright_available: None, ipw_client: httpx.Client
    ):
        """Proxy parameter is accepted even if no proxy is actually running."""
        resp = ipw_client.post(
            "/scrape",
            json={
                "url": EXAMPLE_COM_URL,
                "proxy": "http://nonexistent:9999",
                "timeout": 5000,
            },
        )
        # Without a real proxy, this will likely timeout or fail —
        # either is acceptable behavior
        assert resp.status_code in (200, 502)
