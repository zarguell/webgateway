"""Integration tests for Firecrawl extract + search through the WebGateway.

These tests make real requests to the Firecrawl cloud API (api.firecrawl.dev).
They auto-skip when Firecrawl isn't healthy on the gateway (no API key,
network unreachable, etc.).

All extraction targets https://example.com — a tiny static page that
minimises token usage.
"""

from __future__ import annotations

import httpx

EXAMPLE_COM_URL = "https://example.com"


class TestFirecrawlExtract:
    def test_extract_returns_content(
        self, client: httpx.Client, auth_headers, firecrawl_available: None
    ):
        r = client.post(
            "/extract",
            json={
                "url": EXAMPLE_COM_URL,
                "format": "markdown",
                "provider": "firecrawl",
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["provider_used"] == "firecrawl"
        assert data["url"] == EXAMPLE_COM_URL
        assert len(data["content"]) > 0
        assert data["format"] == "markdown"

    def test_extract_response_schema(
        self, client: httpx.Client, auth_headers, firecrawl_available: None
    ):
        r = client.post(
            "/extract",
            json={"url": EXAMPLE_COM_URL, "provider": "firecrawl"},
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
        assert data["request_id"].startswith("req_")
        assert isinstance(data["latency_ms"], int)
        assert data["latency_ms"] > 0

    def test_extract_request_id_header(
        self, client: httpx.Client, auth_headers, firecrawl_available: None
    ):
        r = client.post(
            "/extract",
            json={"url": EXAMPLE_COM_URL, "provider": "firecrawl"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        header_id = r.headers.get("x-request-id")
        assert header_id is not None
        assert header_id == r.json()["request_id"]


class TestFirecrawlSearch:
    def test_search_returns_results(
        self, client: httpx.Client, auth_headers, firecrawl_available: None
    ):
        r = client.post(
            "/search",
            json={
                "query": "open source programming",
                "num_results": 3,
                "provider": "firecrawl",
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["provider_used"] == "firecrawl"
        assert len(data["results"]) > 0

    def test_search_result_items_have_required_fields(
        self, client: httpx.Client, auth_headers, firecrawl_available: None
    ):
        r = client.post(
            "/search",
            json={
                "query": "wikipedia python language",
                "num_results": 2,
                "provider": "firecrawl",
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        results = r.json()["results"]
        assert len(results) > 0
        for item in results:
            assert "title" in item
            assert "url" in item
            assert "snippet" in item


class TestFirecrawlProviderMetadata:
    def test_firecrawl_appears_in_providers(
        self, client: httpx.Client, auth_headers, firecrawl_available: None
    ):
        r = client.get("/providers", headers=auth_headers)
        assert r.status_code == 200
        providers = r.json()
        firecrawl = next(p for p in providers if p["name"] == "firecrawl")
        assert "extract" in firecrawl["capabilities"]
        assert "search" in firecrawl["capabilities"]
        assert firecrawl["self_hosted"] is False
