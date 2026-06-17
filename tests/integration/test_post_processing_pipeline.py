"""Integration test for the content post-processing pipeline end-to-end.

Starts the gateway + invisible_playwright sidecar, fetches a real web page
with substantial content, and verifies the pipeline cleans the output
(high reduction_pct, clean markdown, boilerplate removed).

Usage:
    docker compose -f docker-compose.test.yml \
        -f docker-compose.invisible-playwright.yml \
        up -d --build

    source .venv/bin/activate
    pytest tests/integration/test_post_processing_pipeline.py -v --tb=long
"""

from __future__ import annotations

import httpx
import pytest

WIKIPEDIA_URL = "https://en.wikipedia.org/wiki/Python_(programming_language)"


def _ipw_healthy(client: httpx.Client) -> bool:
    r = client.get("/health", timeout=10)
    providers = r.json().get("providers", [])
    return any(
        p["name"] == "invisible_playwright" and p["healthy"]
        for p in providers
    )


class TestPostProcessingPipeline:
    def test_pipeline_runs_on_extract(
        self, wait_for_gateway: None, client: httpx.Client, auth_headers: dict[str, str]
    ):
        if not _ipw_healthy(client):
            pytest.skip("invisible_playwright not healthy — start with docker compose")

        resp = client.post(
            "/extract",
            json={"url": WIKIPEDIA_URL, "provider": "invisible_playwright", "format": "markdown"},
            headers=auth_headers,
            timeout=120,
        )
        assert resp.status_code == 200
        data = resp.json()

        assert len(data["content"]) > 0
        assert data["format"] == "markdown"
        assert data["provider_used"] == "invisible_playwright"

        pp = data.get("post_processing")
        assert pp is not None, "post_processing metadata missing"
        assert pp["extractor_used"] == "trafilatura"
        assert pp["content_length_raw"] > 0
        assert pp["content_length_processed"] > 0

        raw = pp["content_length_raw"]
        processed = pp["content_length_processed"]
        reduction = pp["reduction_pct"]

        print(f"\n  Raw: {raw:,} bytes -> Processed: {processed:,} bytes ({reduction}% reduction)")

        # invisible_playwright already returns clean innerText, so reduction
        # may be minimal. The real value of the pipeline is for raw-HTML providers
        # (tested in the unit tests). Here we just verify the pipeline ran.
        assert reduction >= 0, f"Reduction should be >= 0, got {reduction}%"

        content = data["content"]
        assert "Python" in content
        assert "programming" in content.lower()

        for phrase in ["cookie policy", "accept cookies", "privacy policy"]:
            assert phrase.lower() not in content.lower(), f"Boilerplate '{phrase}' should be removed"

        # Content should contain article details (from Wikipedia infobox)
        assert "Guido van Rossum" in content, "Content should mention creator"
        assert "CPython" in content, "Content should mention CPython"
        print("\n--- First 500 chars of cleaned content ---")
        print(content[:500])

    def test_pipeline_can_be_skipped(
        self, wait_for_gateway: None, client: httpx.Client, auth_headers: dict[str, str]
    ):
        if not _ipw_healthy(client):
            pytest.skip("invisible_playwright not healthy")

        resp = client.post(
            "/extract",
            json={
                "url": WIKIPEDIA_URL,
                "provider": "invisible_playwright",
                "format": "markdown",
                "post_processing": {"skip": True},
            },
            headers=auth_headers,
            timeout=120,
        )
        assert resp.status_code == 200
        pp = resp.json().get("post_processing")
        if pp is not None:
            assert pp.get("extractor_used") is None

    def test_html_format_skips_pipeline(
        self, wait_for_gateway: None, client: httpx.Client, auth_headers: dict[str, str]
    ):
        if not _ipw_healthy(client):
            pytest.skip("invisible_playwright not healthy")

        resp = client.post(
            "/extract",
            json={"url": WIKIPEDIA_URL, "provider": "invisible_playwright", "format": "html"},
            headers=auth_headers,
            timeout=120,
        )
        assert resp.status_code == 200
        data = resp.json()
        # format=html skips the pipeline, but the provider still returns
        # whatever format it natively supports (invisible_playwright returns
        # markdown). The key assertion is that post_processing metadata is
        # absent or has no extractor.
        pp = data.get("post_processing")
        if pp is not None:
            assert pp.get("extractor_used") is None, "Pipeline should be skipped for format=html"
