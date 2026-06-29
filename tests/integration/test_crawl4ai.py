"""Integration tests for Crawl4AI through the serpLLM.

Requires the Crawl4AI sidecar running:
    docker compose -f docker-compose.test.yml --profile crawl4ai up -d

Tests auto-skip when the sidecar isn't running.
"""

from __future__ import annotations

import httpx

EXAMPLE_COM_URL = "https://example.com"


class TestCrawl4AIExtract:
    """Tests for crawl4ai (full browser crawl mode)."""

    def test_extract_returns_content(
        self, client: httpx.Client, auth_headers, crawl4ai_available: None
    ):
        r = client.post(
            "/extract",
            json={
                "url": EXAMPLE_COM_URL,
                "format": "markdown",
                "provider": "crawl4ai",
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["provider_used"] == "crawl4ai"
        assert data["url"] == EXAMPLE_COM_URL
        assert len(data["content"]) > 0

    def test_extract_response_schema(
        self, client: httpx.Client, auth_headers, crawl4ai_available: None
    ):
        r = client.post(
            "/extract",
            json={"url": EXAMPLE_COM_URL, "provider": "crawl4ai"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        for field in (
            "content",
            "format",
            "url",
            "provider_used",
            "request_id",
            "latency_ms",
            "cached",
        ):
            assert field in data, f"Missing required field: {field}"

    def test_search_raises_error(
        self, client: httpx.Client, auth_headers, crawl4ai_available: None
    ):
        r = client.post(
            "/search",
            json={"query": "test", "provider": "crawl4ai"},
            headers=auth_headers,
        )
        # ProviderError should trigger fallback; 422 if provider
        # doesn't support search and no fallback is configured.
        assert r.status_code in (422, 502)


class TestCrawl4AIMdExtract:
    """Tests for crawl4ai_md (lightweight markdown mode)."""

    def test_extract_returns_markdown(
        self, client: httpx.Client, auth_headers, crawl4ai_md_available: None
    ):
        r = client.post(
            "/extract",
            json={
                "url": EXAMPLE_COM_URL,
                "format": "markdown",
                "provider": "crawl4ai_md",
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["provider_used"] == "crawl4ai_md"
        assert data["format"] == "markdown"
        assert len(data["content"]) > 0

    def test_extract_response_schema(
        self, client: httpx.Client, auth_headers, crawl4ai_md_available: None
    ):
        r = client.post(
            "/extract",
            json={"url": EXAMPLE_COM_URL, "provider": "crawl4ai_md"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        for field in (
            "content",
            "format",
            "url",
            "provider_used",
            "request_id",
            "latency_ms",
            "cached",
        ):
            assert field in data, f"Missing required field: {field}"


class TestCrawl4AIMetadata:
    def test_crawl4ai_metadata(
        self, client: httpx.Client, auth_headers, crawl4ai_available: None
    ):
        r = client.get("/providers", headers=auth_headers)
        assert r.status_code == 200
        providers = r.json()
        c4 = next(p for p in providers if p["name"] == "crawl4ai")
        assert c4["self_hosted"] is True
        assert "extract" in c4["capabilities"]
        assert "search" not in c4["capabilities"]

    def test_crawl4ai_md_metadata(
        self, client: httpx.Client, auth_headers, crawl4ai_md_available: None
    ):
        r = client.get("/providers", headers=auth_headers)
        assert r.status_code == 200
        providers = r.json()
        c4md = next(p for p in providers if p["name"] == "crawl4ai_md")
        assert c4md["self_hosted"] is True
        assert "extract" in c4md["capabilities"]
        assert "search" not in c4md["capabilities"]

    def test_crawl4ai_and_md_are_separate_providers(
        self, client: httpx.Client, auth_headers, crawl4ai_available: None
    ):
        r = client.get("/providers", headers=auth_headers)
        providers = r.json()
        names = [p["name"] for p in providers]
        assert "crawl4ai" in names
        assert "crawl4ai_md" in names
