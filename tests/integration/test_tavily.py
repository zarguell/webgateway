"""Integration tests for Tavily search + extract through the WebGateway.

These tests make real requests to the Tavily API (api.tavily.com).
They auto-skip when Tavily isn't healthy on the gateway (no API key,
network unreachable, etc.).

All extraction targets https://example.com — a tiny static page that
minimises token usage.
"""

from __future__ import annotations

import httpx

EXAMPLE_COM_URL = "https://example.com"


class TestTavilySearch:
    def test_search_returns_results(
        self, client: httpx.Client, auth_headers, tavily_available: None
    ):
        r = client.post(
            "/search",
            json={
                "query": "python programming language",
                "num_results": 3,
                "provider": "tavily",
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["provider_used"] == "tavily"
        assert len(data["results"]) > 0

    def test_search_result_items_have_required_fields(
        self, client: httpx.Client, auth_headers, tavily_available: None
    ):
        r = client.post(
            "/search",
            json={
                "query": "wikipedia python",
                "num_results": 2,
                "provider": "tavily",
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


class TestTavilyExtract:
    def test_extract_returns_content(
        self, client: httpx.Client, auth_headers, tavily_available: None
    ):
        r = client.post(
            "/extract",
            json={
                "url": EXAMPLE_COM_URL,
                "format": "markdown",
                "provider": "tavily",
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["provider_used"] == "tavily"
        assert data["url"] == EXAMPLE_COM_URL
        assert len(data["content"]) > 0

    def test_extract_response_schema(
        self, client: httpx.Client, auth_headers, tavily_available: None
    ):
        r = client.post(
            "/extract",
            json={"url": EXAMPLE_COM_URL, "provider": "tavily"},
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


class TestTavilyProviderMetadata:
    def test_tavily_appears_in_providers(
        self, client: httpx.Client, auth_headers, tavily_available: None
    ):
        r = client.get("/providers", headers=auth_headers)
        assert r.status_code == 200
        providers = r.json()
        tavily = next(p for p in providers if p["name"] == "tavily")
        assert "search" in tavily["capabilities"]
        assert "extract" in tavily["capabilities"]
        assert tavily["self_hosted"] is False
