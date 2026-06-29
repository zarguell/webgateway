from __future__ import annotations

import httpx
import pytest
from pytest_httpx import HTTPXMock

from serp_llm.providers.base import ExtractOptions, ProviderError, ProviderMetadata
from serp_llm.providers.cdp_chrome import CdpChromeAdapter


@pytest.fixture
def adapter() -> CdpChromeAdapter:
    return CdpChromeAdapter(
        config={
            "base_url": "http://cdp-chrome:9222",
            "timeout": 30,
        }
    )


class TestCdpChromeAdapter:
    async def test_name(self, adapter: CdpChromeAdapter):
        assert adapter.name == "cdp_chrome"

    async def test_metadata(self, adapter: CdpChromeAdapter):
        meta = adapter.metadata
        assert isinstance(meta, ProviderMetadata)
        assert meta.name == "cdp_chrome"
        assert meta.self_hosted is True
        assert meta.specialization == "cdp_browser"
        assert "extract" in meta.capabilities
        assert "search" not in meta.capabilities

    async def test_search_raises_not_supported(
        self, adapter: CdpChromeAdapter
    ):
        with pytest.raises(ProviderError) as exc:
            await adapter.search("test query", ExtractOptions())  # type: ignore[arg-type]
        assert "does not support search" in str(exc.value)

    async def test_extract_success(
        self, adapter: CdpChromeAdapter, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="POST",
            url="http://cdp-chrome:9222/extract",
            json={
                "content": "# Example Domain\n\nThis domain is for use in illustrative examples.",
                "format": "markdown",
                "url": "https://example.com",
                "title": "Example Domain",
            },
        )
        result = await adapter.extract(
            "https://example.com",
            ExtractOptions(),
        )
        assert "# Example Domain" in result.content
        assert result.format == "markdown"
        assert result.url == "https://example.com"
        assert result.title == "Example Domain"

    async def test_extract_chrome_not_connected(
        self, adapter: CdpChromeAdapter, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="POST",
            url="http://cdp-chrome:9222/extract",
            status_code=503,
            json={"detail": "Chrome not connected"},
        )
        with pytest.raises(ProviderError) as exc:
            await adapter.extract(
                "https://example.com",
                ExtractOptions(),
            )
        assert exc.value.status_code == 503
        assert exc.value.error_class == "service_unavailable"

    async def test_extract_http_error(
        self, adapter: CdpChromeAdapter, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="POST",
            url="http://cdp-chrome:9222/extract",
            status_code=500,
        )
        with pytest.raises(ProviderError) as exc:
            await adapter.extract(
                "https://example.com",
                ExtractOptions(),
            )
        assert exc.value.status_code == 500

    async def test_extract_connection_error(
        self, adapter: CdpChromeAdapter, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_exception(
            httpx.ConnectError("Connection refused"),
        )
        with pytest.raises(ProviderError) as exc:
            await adapter.extract(
                "https://example.com",
                ExtractOptions(),
            )
        assert "Request failed" in str(exc.value)

    async def test_health_check_success(
        self, adapter: CdpChromeAdapter, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="GET",
            url="http://cdp-chrome:9222/health",
            json={"status": "ok"},
        )
        assert await adapter.health_check() is True

    async def test_health_check_failure(
        self, adapter: CdpChromeAdapter, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_exception(
            httpx.ConnectError("Connection refused"),
        )
        assert await adapter.health_check() is False

    async def test_health_check_unhealthy_status(
        self, adapter: CdpChromeAdapter, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="GET",
            url="http://cdp-chrome:9222/health",
            json={"status": "error"},
        )
        assert await adapter.health_check() is False

    async def test_default_config(self):
        adapter = CdpChromeAdapter()
        assert adapter._base_url == "http://localhost:9222"
        assert adapter._timeout == 30
