"""Integration tests for Zyte extraction through the WebGateway.

These tests make real requests to api.zyte.com. They auto-skip when
Zyte isn't healthy on the gateway (no API key, network unreachable, etc.).

All extraction targets https://example.com — a tiny static page that
minimises token usage.
"""

from __future__ import annotations

import httpx

EXAMPLE_COM_URL = "https://example.com"


class TestZyteExtract:
    def test_extract_returns_content(
        self, client: httpx.Client, auth_headers, zyte_available: None
    ):
        r = client.post(
            "/extract",
            json={
                "url": EXAMPLE_COM_URL,
                "format": "markdown",
                "provider": "zyte",
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["provider_used"] == "zyte"
        assert data["url"] == EXAMPLE_COM_URL
        assert len(data["content"]) > 0
        assert data["format"] == "markdown"

    def test_extract_response_schema(
        self, client: httpx.Client, auth_headers, zyte_available: None
    ):
        r = client.post(
            "/extract",
            json={"url": EXAMPLE_COM_URL, "provider": "zyte"},
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

    def test_extract_html_format(
        self, client: httpx.Client, auth_headers, zyte_available: None
    ):
        r = client.post(
            "/extract",
            json={"url": EXAMPLE_COM_URL, "format": "html", "provider": "zyte"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["format"] == "html"
        assert len(data["content"]) > 0

    def test_extract_appears_in_providers(
        self, client: httpx.Client, auth_headers, zyte_available: None
    ):
        r = client.get("/providers", headers=auth_headers)
        assert r.status_code == 200
        providers = r.json()
        zyte = next(p for p in providers if p["name"] == "zyte")
        assert "extract" in zyte["capabilities"]
