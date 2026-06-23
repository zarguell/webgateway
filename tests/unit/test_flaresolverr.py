from __future__ import annotations

import json

import httpx
import pytest
from pytest_httpx import HTTPXMock

from webgateway.providers.base import ExtractOptions, ProviderError, ProviderMetadata
from webgateway.providers.flaresolverr import FlareSolverrAdapter


@pytest.fixture
def adapter() -> FlareSolverrAdapter:
    return FlareSolverrAdapter(
        config={
            "base_url": "http://flaresolverr:8191",
            "max_timeout": 60000,
        }
    )


class TestFlareSolverrAdapter:
    async def test_name(self, adapter: FlareSolverrAdapter):
        assert adapter.name == "flaresolverr"

    async def test_metadata(self, adapter: FlareSolverrAdapter):
        meta = adapter.metadata
        assert isinstance(meta, ProviderMetadata)
        assert meta.name == "flaresolverr"
        assert meta.self_hosted is True
        assert "extract" in meta.capabilities
        assert "search" not in meta.capabilities

    async def test_search_raises_not_supported(
        self, adapter: FlareSolverrAdapter
    ):
        with pytest.raises(ProviderError) as exc:
            await adapter.search("test query", ExtractOptions())  # type: ignore[arg-type]
        assert "does not support search" in str(exc.value)

    async def test_extract_success(
        self, adapter: FlareSolverrAdapter, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="POST",
            url="http://flaresolverr:8191/v1",
            json={
                "status": "ok",
                "message": "Challenge solved!",
                "solution": {
                    "url": "https://example.com/protected-page",
                    "status": 200,
                    "headers": {},
                    "response": "<html><body>Protected content</body></html>",
                },
            },
        )
        result = await adapter.extract(
            "https://example.com/protected-page",
            ExtractOptions(),
        )
        assert result.content == "<html><body>Protected content</body></html>"
        assert result.format == "html"
        assert result.url == "https://example.com/protected-page"

    async def test_extract_challenge_error(
        self, adapter: FlareSolverrAdapter, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="POST",
            url="http://flaresolverr:8191/v1",
            json={
                "status": "error",
                "message": "Error: timeout solving challenge",
            },
        )
        with pytest.raises(ProviderError) as exc:
            await adapter.extract(
                "https://example.com/protected",
                ExtractOptions(),
            )
        assert "timeout solving challenge" in str(exc.value)

    async def test_extract_connection_error(
        self, adapter: FlareSolverrAdapter, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_exception(
            httpx.ConnectError("Connection refused"),
        )
        with pytest.raises(ProviderError) as exc:
            await adapter.extract(
                "https://example.com/protected",
                ExtractOptions(),
            )
        assert "Request failed" in str(exc.value)

    async def test_extract_http_error(
        self, adapter: FlareSolverrAdapter, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="POST",
            url="http://flaresolverr:8191/v1",
            status_code=500,
        )
        with pytest.raises(ProviderError) as exc:
            await adapter.extract(
                "https://example.com/protected",
                ExtractOptions(),
            )
        assert exc.value.status_code == 500

    async def test_health_check_success(
        self, adapter: FlareSolverrAdapter, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="GET",
            url="http://flaresolverr:8191/health",
            json={"status": "ok"},
        )
        assert await adapter.health_check() is True

    async def test_health_check_failure(
        self, adapter: FlareSolverrAdapter, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_exception(
            httpx.ConnectError("Connection refused"),
        )
        assert await adapter.health_check() is False

    async def test_health_check_wrong_status(
        self, adapter: FlareSolverrAdapter, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="GET",
            url="http://flaresolverr:8191/health",
            json={"status": "error"},
        )
        assert await adapter.health_check() is False

    async def test_proxy_passthrough(
        self, adapter: FlareSolverrAdapter, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="POST",
            url="http://flaresolverr:8191/v1",
            json={
                "status": "ok",
                "solution": {
                    "url": "https://example.com/page",
                    "response": "<html>content</html>",
                },
            },
        )
        await adapter.extract(
            "https://example.com/page",
            ExtractOptions(proxy_url="http://proxy:8080"),
        )
        request = httpx_mock.get_request()
        body = json.loads(request.content)
        assert body["proxy"] == {"url": "http://proxy:8080"}

    async def test_proxy_not_included_when_not_provided(
        self, adapter: FlareSolverrAdapter, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="POST",
            url="http://flaresolverr:8191/v1",
            json={
                "status": "ok",
                "solution": {
                    "url": "https://example.com/page",
                    "response": "<html>content</html>",
                },
            },
        )
        await adapter.extract(
            "https://example.com/page",
            ExtractOptions(),
        )
        request = httpx_mock.get_request()
        body = json.loads(request.content)
        assert "proxy" not in body

    async def test_default_config(self):
        adapter = FlareSolverrAdapter()
        assert adapter._base_url == "http://localhost:8191"
        assert adapter._max_timeout == 60000
