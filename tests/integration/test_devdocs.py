"""Integration tests for DevDocs search through the serpLLM.

Auto-skips when DevDocs isn't healthy on the gateway (no container,
network unreachable).

Requires DevDocs container running:
  docker compose -f docker-compose.test.yml --profile docs up -d
"""

from __future__ import annotations

import httpx


class TestDevDocsSearch:
    def test_search_returns_results(
        self, client: httpx.Client, auth_headers, devdocs_available: None
    ):
        r = client.post(
            "/search",
            json={
                "query": "array map",
                "num_results": 3,
                "provider": "devdocs",
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["provider_used"] == "devdocs"

    def test_search_response_has_metadata(
        self, client: httpx.Client, auth_headers, devdocs_available: None
    ):
        r = client.post(
            "/search",
            json={
                "query": "async await",
                "num_results": 1,
                "provider": "devdocs",
            },
            headers=auth_headers,
        )
        data = r.json()
        assert data["provider_used"] == "devdocs"
        assert "request_id" in data
        assert "latency_ms" in data

    def test_devdocs_appears_in_providers(
        self, client: httpx.Client, auth_headers, devdocs_available: None
    ):
        r = client.get("/providers", headers=auth_headers)
        assert r.status_code == 200
        providers = r.json()
        dd = next(p for p in providers if p["name"] == "devdocs")
        assert "search" in dd["capabilities"]
        assert dd["self_hosted"] is True
        assert dd["specialization"] == "docs"
