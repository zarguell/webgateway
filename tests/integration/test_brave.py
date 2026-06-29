"""Integration tests for Brave Search through the serpLLM.

These tests make real requests to the Brave Search API
(api.search.brave.com). They auto-skip when Brave isn't healthy
on the gateway (no API key, network unreachable, etc.).
"""

from __future__ import annotations

import httpx


class TestBraveSearch:
    def test_search_returns_results(
        self, client: httpx.Client, auth_headers, brave_available: None
    ):
        r = client.post(
            "/search",
            json={
                "query": "python programming language",
                "num_results": 3,
                "provider": "brave",
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["provider_used"] == "brave"
        assert len(data["results"]) > 0

    def test_search_result_items_have_required_fields(
        self, client: httpx.Client, auth_headers, brave_available: None
    ):
        r = client.post(
            "/search",
            json={
                "query": "wikipedia python",
                "num_results": 2,
                "provider": "brave",
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

    def test_search_response_has_metadata(
        self, client: httpx.Client, auth_headers, brave_available: None
    ):
        r = client.post(
            "/search",
            json={
                "query": "fastapi python framework",
                "num_results": 1,
                "provider": "brave",
            },
            headers=auth_headers,
        )
        data = r.json()
        assert data["provider_used"] == "brave"
        assert "request_id" in data
        assert "latency_ms" in data
        assert data["request_id"].startswith("req_")
        assert isinstance(data["latency_ms"], int)
        assert data["latency_ms"] > 0

    def test_search_request_id_in_response_header(
        self, client: httpx.Client, auth_headers, brave_available: None
    ):
        r = client.post(
            "/search",
            json={
                "query": "httpx async python",
                "num_results": 1,
                "provider": "brave",
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        header_id = r.headers.get("x-request-id")
        assert header_id is not None
        assert header_id == r.json()["request_id"]

    def test_num_results_limits_output(
        self, client: httpx.Client, auth_headers, brave_available: None
    ):
        r = client.post(
            "/search",
            json={
                "query": "machine learning",
                "num_results": 2,
                "provider": "brave",
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert len(r.json()["results"]) <= 2


class TestBraveProviderMetadata:
    def test_brave_appears_in_providers(
        self, client: httpx.Client, auth_headers, brave_available: None
    ):
        r = client.get("/providers", headers=auth_headers)
        assert r.status_code == 200
        providers = r.json()
        brave = next(p for p in providers if p["name"] == "brave")
        assert "search" in brave["capabilities"]
        assert brave["self_hosted"] is False
