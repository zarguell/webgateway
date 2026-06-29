"""Integration tests for self-hosted Firecrawl through the serpLLM.

These tests run against a local Firecrawl stack (5 containers) started via:
    docker compose -f docker-compose.test.yml --profile firecrawl-selfhosted up

They auto-skip when the self-hosted instance isn't running, so they add
zero overhead to the standard test suite.
"""

from __future__ import annotations

import httpx

EXAMPLE_COM_URL = "https://example.com"


class TestFirecrawlSelfHostedExtract:
    def test_extract_returns_content(
        self, client: httpx.Client, auth_headers, firecrawl_selfhosted_available: None
    ):
        r = client.post(
            "/extract",
            json={
                "url": EXAMPLE_COM_URL,
                "format": "markdown",
                "provider": "firecrawl_selfhosted",
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["provider_used"] == "firecrawl_selfhosted"
        assert data["url"] == EXAMPLE_COM_URL
        assert len(data["content"]) > 0

    def test_extract_response_schema(
        self, client: httpx.Client, auth_headers, firecrawl_selfhosted_available: None
    ):
        r = client.post(
            "/extract",
            json={"url": EXAMPLE_COM_URL, "provider": "firecrawl_selfhosted"},
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


class TestFirecrawlSelfHostedSearch:
    def test_search_returns_results(
        self, client: httpx.Client, auth_headers, firecrawl_selfhosted_available: None
    ):
        r = client.post(
            "/search",
            json={
                "query": "open source programming",
                "num_results": 3,
                "provider": "firecrawl_selfhosted",
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["provider_used"] == "firecrawl_selfhosted"
        assert len(data["results"]) > 0


class TestFirecrawlSelfHostedMetadata:
    def test_self_hosted_flag_is_true(
        self, client: httpx.Client, auth_headers, firecrawl_selfhosted_available: None
    ):
        r = client.get("/providers", headers=auth_headers)
        assert r.status_code == 200
        providers = r.json()
        fc = next(
            p for p in providers if p["name"] == "firecrawl_selfhosted"
        )
        assert fc["self_hosted"] is True
        assert "extract" in fc["capabilities"]
        assert "search" in fc["capabilities"]
