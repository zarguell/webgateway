from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from serp_llm.providers.base import ProviderError
from serp_llm.providers.exa import ExaAdapter


@pytest.fixture
def adapter() -> ExaAdapter:
    return ExaAdapter(api_key="test-key", timeout=15)


_SEARCH_RESPONSE = {
    "requestId": "req_123",
    "results": [
        {
            "title": "Quantum Computing Breakthrough",
            "url": "https://example.com/quantum",
            "id": "https://example.com/quantum",
            "publishedDate": "2024-06-01T00:00:00.000Z",
            "author": "Dr. Smith",
            "score": 0.95,
            "highlights": ["New quantum processor achieves 1000 qubits"],
            "text": "Full article text about quantum computing...",
        },
        {
            "title": "AI Advances 2024",
            "url": "https://example.com/ai",
            "publishedDate": None,
            "score": 0.88,
            "text": "Recent advances in artificial intelligence...",
        },
    ],
    "costDollars": {"total": 0.007},
}

_CONTENTS_RESPONSE = {
    "results": [
        {
            "title": "Page Title",
            "url": "https://example.com/article",
            "text": "# Article\n\nFull content here.",
        }
    ]
}


class TestExaAdapter:
    async def test_name(self, adapter: ExaAdapter):
        assert adapter.name == "exa"

    async def test_metadata(self, adapter: ExaAdapter):
        meta = adapter.metadata
        assert meta.name == "exa"
        assert meta.specialization == "semantic"
        assert "search" in meta.capabilities
        assert "extract" in meta.capabilities

    async def test_search_success(
        self, adapter: ExaAdapter, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="POST",
            url="https://api.exa.ai/search",
            json=_SEARCH_RESPONSE,
        )

        result = await adapter.search("quantum computing", options=None)  # type: ignore[arg-type]
        assert len(result.results) == 2
        assert result.results[0].title == "Quantum Computing Breakthrough"
        assert "example.com" in result.results[0].url
        assert result.results[0].published_date == "2024-06-01T00:00:00.000Z"

    async def test_search_no_key(self):
        no_key = ExaAdapter(api_key=None)
        with pytest.raises(ProviderError) as exc:
            await no_key.search("test", options=None)  # type: ignore[arg-type]
        assert "API key required" in str(exc.value)

    async def test_search_rate_limit(
        self, adapter: ExaAdapter, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="POST",
            url="https://api.exa.ai/search",
            status_code=429,
        )
        with pytest.raises(ProviderError) as exc:
            await adapter.search("test", options=None)  # type: ignore[arg-type]
        assert exc.value.error_class == "rate_limited"

    async def test_search_bad_key(
        self, adapter: ExaAdapter, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="POST",
            url="https://api.exa.ai/search",
            status_code=401,
        )
        with pytest.raises(ProviderError) as exc:
            await adapter.search("test", options=None)  # type: ignore[arg-type]
        assert exc.value.status_code == 401

    async def test_extract_success(
        self, adapter: ExaAdapter, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="POST",
            url="https://api.exa.ai/contents",
            json=_CONTENTS_RESPONSE,
        )

        result = await adapter.extract(
            "https://example.com/article",
            options=None,  # type: ignore[arg-type]
        )
        assert "Article" in result.content
        assert result.title == "Page Title"
        assert result.url == "https://example.com/article"

    async def test_extract_no_key(self):
        no_key = ExaAdapter(api_key=None)
        with pytest.raises(ProviderError) as exc:
            await no_key.extract("http://example.com", options=None)  # type: ignore[arg-type]
        assert "API key required" in str(exc.value)

    async def test_health_check_ok(self, adapter: ExaAdapter, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            method="POST",
            url="https://api.exa.ai/search",
            status_code=200,
        )
        assert await adapter.health_check() is True

    async def test_health_check_bad_key(self, adapter: ExaAdapter, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            method="POST",
            url="https://api.exa.ai/search",
            status_code=401,
        )
        assert await adapter.health_check() is True

    async def test_health_check_network_error(self, adapter: ExaAdapter, httpx_mock: HTTPXMock):
        httpx_mock.add_exception(ConnectionError("connection refused"))
        assert await adapter.health_check() is False
