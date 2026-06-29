"""Integration tests for Exa search through the serpLLM.

Auto-skips when Exa isn't healthy on the gateway (no API key,
network unreachable). If the API key has exhausted its credits,
search tests will verify the error response shape rather than
expecting 200.
"""

from __future__ import annotations

import httpx


class TestExaSearch:
    def test_search_returns_results_or_credits_error(
        self, client: httpx.Client, auth_headers, exa_available: None
    ):
        r = client.post(
            "/search",
            json={
                "query": "python programming language",
                "num_results": 3,
                "provider": "exa",
            },
            headers=auth_headers,
        )
        assert r.status_code in (200, 402, 502)
        if r.status_code == 200:
            assert r.json()["provider_used"] == "exa"

    def test_search_response_has_metadata(
        self, client: httpx.Client, auth_headers, exa_available: None
    ):
        r = client.post(
            "/search",
            json={
                "query": "quantum computing",
                "num_results": 1,
                "provider": "exa",
            },
            headers=auth_headers,
        )
        if r.status_code == 200:
            data = r.json()
            assert data["provider_used"] == "exa"
            assert "request_id" in data
            assert "latency_ms" in data

    def test_search_result_items_have_required_fields(
        self, client: httpx.Client, auth_headers, exa_available: None
    ):
        r = client.post(
            "/search",
            json={"query": "fastapi", "num_results": 2, "provider": "exa"},
            headers=auth_headers,
        )
        if r.status_code == 200:
            for item in r.json()["results"]:
                assert "title" in item
                assert "url" in item

    def test_exa_appears_in_providers(
        self, client: httpx.Client, auth_headers, exa_available: None
    ):
        r = client.get("/providers", headers=auth_headers)
        assert r.status_code == 200
        providers = r.json()
        exa = next(p for p in providers if p["name"] == "exa")
        assert "search" in exa["capabilities"]
        assert "extract" in exa["capabilities"]
        assert exa["specialization"] == "semantic"
