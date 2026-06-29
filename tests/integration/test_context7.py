"""Integration tests for Context7 docs search through the serpLLM.

Auto-skips when Context7 isn't healthy on the gateway (no API key,
network unreachable).
"""

from __future__ import annotations

import httpx


class TestContext7Search:
    def test_search_returns_results(
        self, client: httpx.Client, auth_headers, context7_available: None
    ):
        r = client.post(
            "/search",
            json={
                "query": "FastAPI middleware",
                "num_results": 3,
                "provider": "context7",
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["provider_used"] == "context7"
        # May be empty if library name not detected, but the call succeeds

    def test_search_response_has_metadata(
        self, client: httpx.Client, auth_headers, context7_available: None
    ):
        r = client.post(
            "/search",
            json={
                "query": "Python fastapi routing",
                "num_results": 1,
                "provider": "context7",
            },
            headers=auth_headers,
        )
        data = r.json()
        assert data["provider_used"] == "context7"
        assert "request_id" in data
        assert "latency_ms" in data
        assert data["request_id"].startswith("req_")

    def test_context7_appears_in_providers(
        self, client: httpx.Client, auth_headers, context7_available: None
    ):
        r = client.get("/providers", headers=auth_headers)
        assert r.status_code == 200
        providers = r.json()
        ctx = next(p for p in providers if p["name"] == "context7")
        assert "search" in ctx["capabilities"]
        assert ctx["self_hosted"] is False
        assert ctx["mcp_native"] is True
