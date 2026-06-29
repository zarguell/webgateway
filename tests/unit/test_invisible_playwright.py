from __future__ import annotations

import json

import pytest
from pytest_httpx import HTTPXMock

from serp_llm.providers.base import ExtractOptions, ProviderError, ProviderMetadata
from serp_llm.providers.invisible_playwright import InvisiblePlaywrightAdapter


@pytest.fixture
def adapter() -> InvisiblePlaywrightAdapter:
    return InvisiblePlaywrightAdapter(
        base_url="http://invisible-playwright:3001",
        timeout=15,
    )


class TestInvisiblePlaywrightAdapter:
    async def test_name(self, adapter: InvisiblePlaywrightAdapter):
        assert adapter.name == "invisible_playwright"

    async def test_metadata(self, adapter: InvisiblePlaywrightAdapter):
        meta = adapter.metadata
        assert isinstance(meta, ProviderMetadata)
        assert meta.name == "invisible_playwright"
        assert meta.self_hosted is True
        assert meta.stealth is True
        assert meta.engine == "firefox"
        assert "extract" in meta.capabilities
        assert "search" not in meta.capabilities

    async def test_search_raises_not_supported(
        self, adapter: InvisiblePlaywrightAdapter
    ):
        with pytest.raises(ProviderError) as exc:
            await adapter.search("test query", ExtractOptions())
        assert exc.value.error_class == "not_supported"

    async def test_extract_success(
        self, adapter: InvisiblePlaywrightAdapter, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="POST",
            url="http://invisible-playwright:3001/scrape",
            json={
                "content": "# Hello World\n\nThis is the article.",
                "format": "markdown",
                "url": "https://example.com/article",
                "title": "Hello World Article",
            },
        )
        result = await adapter.extract(
            "https://example.com/article",
            ExtractOptions(),
        )
        assert result.content == "# Hello World\n\nThis is the article."
        assert result.format == "markdown"
        assert result.title == "Hello World Article"

    async def test_extract_with_session(
        self, adapter: InvisiblePlaywrightAdapter, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="POST",
            url="http://invisible-playwright:3001/scrape",
            json={
                "content": "auth content",
                "format": "markdown",
                "url": "https://wsj.com/article",
            },
        )
        result = await adapter.extract(
            "https://wsj.com/article",
            ExtractOptions(
                session_id="wsj_session_abc",
                session_cookies={"sessionid": "xyz"},
                fingerprint_id="fp_03",
                user_agent="Mozilla/5.0 Firefox/150.0",
                proxy_url="http://residential:24000",
                wait_for_selector=".article-body",
            ),
        )
        assert result.content == "auth content"
        request = httpx_mock.get_request()
        body = json.loads(request.content)
        assert body["session_id"] == "wsj_session_abc"
        assert body["fingerprint"] == "fp_03"
        assert body["wait_for_selector"] == ".article-body"

    async def test_extract_http_error(
        self, adapter: InvisiblePlaywrightAdapter, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="POST",
            url="http://invisible-playwright:3001/scrape",
            status_code=500,
        )
        with pytest.raises(ProviderError) as exc:
            await adapter.extract("https://example.com/article", ExtractOptions())
        assert exc.value.status_code == 500

    async def test_health_check_success(
        self, adapter: InvisiblePlaywrightAdapter, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="GET",
            url="http://invisible-playwright:3001/health",
            status_code=200,
        )
        assert await adapter.health_check() is True

    async def test_health_check_failure(
        self, adapter: InvisiblePlaywrightAdapter, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="GET",
            url="http://invisible-playwright:3001/health",
            status_code=503,
        )
        assert await adapter.health_check() is False
