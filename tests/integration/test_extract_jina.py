"""Integration tests for Jina Reader extraction through the serpLLM.

These tests make real requests to r.jina.ai. They auto-skip when
JINA_API_KEY is not set in the environment, so they add zero cost
unless explicitly opted in.

All extraction targets https://example.com — a tiny static page that
costs roughly 100 tokens per request.
"""

from __future__ import annotations

import httpx

EXAMPLE_COM_URL = "https://example.com"


class TestJinaExtract:
    def test_extract_returns_content(
        self, client: httpx.Client, auth_headers, jina_available: None
    ):
        r = client.post(
            "/extract",
            json={"url": EXAMPLE_COM_URL, "format": "markdown"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["provider_used"] == "jina"
        assert data["url"] == EXAMPLE_COM_URL
        assert len(data["content"]) > 0
        assert data["format"] == "markdown"

    def test_extract_response_schema(
        self, client: httpx.Client, auth_headers, jina_available: None
    ):
        r = client.post(
            "/extract",
            json={"url": EXAMPLE_COM_URL},
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

    def test_extract_request_id_header(
        self, client: httpx.Client, auth_headers, jina_available: None
    ):
        r = client.post(
            "/extract",
            json={"url": EXAMPLE_COM_URL},
            headers=auth_headers,
        )
        assert r.status_code == 200
        header_id = r.headers.get("x-request-id")
        assert header_id is not None
        assert header_id == r.json()["request_id"]

    def test_extract_html_format(
        self, client: httpx.Client, auth_headers, jina_available: None
    ):
        r = client.post(
            "/extract",
            json={"url": EXAMPLE_COM_URL, "format": "html"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["format"] == "html"
        assert len(data["content"]) > 0

    def test_extract_appears_in_providers(
        self, client: httpx.Client, auth_headers, jina_available: None
    ):
        r = client.get("/providers", headers=auth_headers)
        assert r.status_code == 200
        providers = r.json()
        jina = next(p for p in providers if p["name"] == "jina")
        assert "extract" in jina["capabilities"]
