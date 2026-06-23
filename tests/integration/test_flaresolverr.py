"""Integration tests for FlareSolverr extraction through the WebGateway.

These tests make real requests to the FlareSolverr service running alongside
the gateway. They auto-skip when FlareSolverr isn't healthy, so they add
zero cost unless the full test stack is running.

All extraction targets https://example.com — a tiny static page that
costs roughly 100 tokens per request.
"""

from __future__ import annotations

import httpx

EXAMPLE_COM_URL = "https://example.com"


class TestFlareSolverrExtract:
    def test_extract_returns_content(
        self, client: httpx.Client, auth_headers, flaresolverr_available: None
    ):
        r = client.post(
            "/extract",
            json={"url": EXAMPLE_COM_URL, "format": "markdown", "provider": "flaresolverr"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["provider_used"] == "flaresolverr"
        assert data["url"] == EXAMPLE_COM_URL
        assert len(data["content"]) > 0
        assert data["format"] == "markdown"

    def test_extract_response_schema(
        self, client: httpx.Client, auth_headers, flaresolverr_available: None
    ):
        r = client.post(
            "/extract",
            json={"url": EXAMPLE_COM_URL, "provider": "flaresolverr"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        for field in ("content", "format", "url", "provider_used",
                      "request_id", "latency_ms", "cached"):
            assert field in data, f"Missing required field: {field}"
        assert data["request_id"].startswith("req_")
        assert isinstance(data["latency_ms"], int)
        assert data["latency_ms"] > 0

    def test_extract_html_format(
        self, client: httpx.Client, auth_headers, flaresolverr_available: None
    ):
        r = client.post(
            "/extract",
            json={"url": EXAMPLE_COM_URL, "format": "html", "provider": "flaresolverr"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["format"] == "html"
        assert len(data["content"]) > 0

    def test_extract_appears_in_providers(
        self, client: httpx.Client, auth_headers, flaresolverr_available: None
    ):
        r = client.get("/providers", headers=auth_headers)
        assert r.status_code == 200
        providers = r.json()
        flaresolverr = next(p for p in providers if p["name"] == "flaresolverr")
        assert "extract" in flaresolverr["capabilities"]
