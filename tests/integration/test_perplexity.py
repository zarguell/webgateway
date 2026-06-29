"""Integration tests for Perplexity search through the serpLLM.

Auto-skips when Perplexity isn't healthy on the gateway (no API key,
network unreachable).
"""

from __future__ import annotations

import httpx


class TestPerplexitySearch:
    def test_search_returns_results(
        self, client: httpx.Client, auth_headers, perplexity_available: None
    ):
        r = client.post(
            "/search",
            json={
                "query": "python programming language",
                "num_results": 3,
                "provider": "perplexity",
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["provider_used"] == "perplexity"

    def test_search_response_has_metadata(
        self, client: httpx.Client, auth_headers, perplexity_available: None
    ):
        r = client.post(
            "/search",
            json={
                "query": "latest AI research",
                "num_results": 1,
                "provider": "perplexity",
            },
            headers=auth_headers,
        )
        data = r.json()
        assert data["provider_used"] == "perplexity"
        assert "request_id" in data
        assert "latency_ms" in data
        assert data["request_id"].startswith("req_")

    def test_perplexity_appears_in_providers(
        self, client: httpx.Client, auth_headers, perplexity_available: None
    ):
        r = client.get("/providers", headers=auth_headers)
        assert r.status_code == 200
        providers = r.json()
        ppl = next(p for p in providers if p["name"] == "perplexity")
        assert "search" in ppl["capabilities"]
        assert ppl["self_hosted"] is False
