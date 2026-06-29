from __future__ import annotations

import base64

import pytest
from pytest_httpx import HTTPXMock

from serp_llm.providers.base import ExtractOptions, ProviderError
from serp_llm.providers.zyte import ZyteAdapter


@pytest.fixture
def adapter() -> ZyteAdapter:
    return ZyteAdapter(api_key="test-key", timeout=15)


@pytest.fixture
def no_key_adapter() -> ZyteAdapter:
    return ZyteAdapter(api_key=None, timeout=15)


_EXTRACT_RESPONSE = {
    "browserHtml": "<html><body><h1>Hello World</h1></body></html>",
    "url": "https://example.com/final",
}


class TestZyteAdapter:
    async def test_name(self, adapter: ZyteAdapter):
        assert adapter.name == "zyte"

    async def test_metadata(self, adapter: ZyteAdapter):
        meta = adapter.metadata
        assert meta.name == "zyte"
        assert meta.self_hosted is False
        assert "extract" in meta.capabilities

    async def test_extract_success(
        self, adapter: ZyteAdapter, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="POST",
            url="https://api.zyte.com/v1/extract",
            json=_EXTRACT_RESPONSE,
        )

        result = await adapter.extract(
            "https://example.com",
            options=ExtractOptions(),
        )
        assert "Hello World" in result.content
        assert result.url == "https://example.com/final"
        assert result.format == "html"

    async def test_extract_auth_error(
        self, adapter: ZyteAdapter, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="POST",
            url="https://api.zyte.com/v1/extract",
            status_code=401,
        )
        with pytest.raises(ProviderError) as exc:
            await adapter.extract(
                "https://example.com",
                options=ExtractOptions(),
            )
        assert "authentication failed" in str(exc.value)

    async def test_extract_rate_limit(
        self, adapter: ZyteAdapter, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="POST",
            url="https://api.zyte.com/v1/extract",
            status_code=429,
        )
        with pytest.raises(ProviderError) as exc:
            await adapter.extract(
                "https://example.com",
                options=ExtractOptions(),
            )
        assert "rate limit exceeded" in str(exc.value)

    async def test_extract_ban(
        self, adapter: ZyteAdapter, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="POST",
            url="https://api.zyte.com/v1/extract",
            status_code=520,
        )
        with pytest.raises(ProviderError) as exc:
            await adapter.extract(
                "https://example.com",
                options=ExtractOptions(),
            )
        assert "ban detected" in str(exc.value)

    async def test_search_raises(self, adapter: ZyteAdapter):
        with pytest.raises(ProviderError) as exc:
            await adapter.search("test", options=None)  # type: ignore[arg-type]
        assert "does not support search" in str(exc.value)

    async def test_health_check_with_key(self, adapter: ZyteAdapter):
        assert await adapter.health_check() is True

    async def test_health_check_no_key(self, no_key_adapter: ZyteAdapter):
        assert await no_key_adapter.health_check() is False

    async def test_basic_auth_header_format(
        self, adapter: ZyteAdapter, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="POST",
            url="https://api.zyte.com/v1/extract",
            json=_EXTRACT_RESPONSE,
        )

        await adapter.extract("https://example.com", options=ExtractOptions())

        request = httpx_mock.get_requests()[0]
        auth_header = request.headers.get("Authorization", "")
        assert auth_header.startswith("Basic ")
        encoded = auth_header.removeprefix("Basic ")
        decoded = base64.b64decode(encoded).decode()
        assert decoded == "test-key:"

    async def test_extract_request_body(
        self, adapter: ZyteAdapter, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="POST",
            url="https://api.zyte.com/v1/extract",
            json=_EXTRACT_RESPONSE,
        )

        await adapter.extract("https://example.com", options=ExtractOptions())

        request = httpx_mock.get_requests()[0]
        body = request.read()
        import json

        payload = json.loads(body)
        assert payload["url"] == "https://example.com"
        assert payload["browserHtml"] is True
