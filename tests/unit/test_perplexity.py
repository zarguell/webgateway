from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from serp_llm.providers.base import ProviderError
from serp_llm.providers.perplexity import PerplexityAdapter


@pytest.fixture
def adapter() -> PerplexityAdapter:
    return PerplexityAdapter(api_key="test-key", timeout=15)


_SEARCH_RESPONSE = {
    "citations": ["https://example.com/ai-research-2024", "https://arxiv.org/abs/2401.12345"],
    "search_results": [
        {
            "title": "AI Research 2024",
            "url": "https://example.com/ai-research-2024",
            "snippet": "Key breakthroughs in AI...",
            "date": "2024-06-01",
        },
    ],
    "choices": [
        {
            "message": {
                "role": "assistant",
                "content": "Recent AI research has focused on large language models [1][2].",
            }
        }
    ],
    "usage": {"prompt_tokens": 9, "completion_tokens": 42, "total_tokens": 51},
}


class TestPerplexityAdapter:
    async def test_name(self, adapter: PerplexityAdapter):
        assert adapter.name == "perplexity"

    async def test_metadata(self, adapter: PerplexityAdapter):
        meta = adapter.metadata
        assert meta.name == "perplexity"
        assert meta.specialization == "ai_native"
        assert "search" in meta.capabilities

    async def test_search_success(
        self, adapter: PerplexityAdapter, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="POST",
            url="https://api.perplexity.ai/v1/sonar",
            json=_SEARCH_RESPONSE,
        )

        result = await adapter.search("AI research 2024", options=None)  # type: ignore[arg-type]
        assert len(result.results) > 0
        assert result.results[0].title == "AI Research 2024"
        assert "example.com" in result.results[0].url

    async def test_search_citations_fallback(
        self, adapter: PerplexityAdapter, httpx_mock: HTTPXMock
    ):
        resp = dict(_SEARCH_RESPONSE)
        resp["search_results"] = []
        httpx_mock.add_response(
            method="POST",
            url="https://api.perplexity.ai/v1/sonar",
            json=resp,
        )

        result = await adapter.search("test", options=None)  # type: ignore[arg-type]
        assert len(result.results) == 2
        assert result.results[0].title == "Source 1"

    async def test_search_no_key(self):
        no_key = PerplexityAdapter(api_key=None)
        with pytest.raises(ProviderError) as exc:
            await no_key.search("test", options=None)  # type: ignore[arg-type]
        assert "API key required" in str(exc.value)

    async def test_search_rate_limit(
        self, adapter: PerplexityAdapter, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="POST",
            url="https://api.perplexity.ai/v1/sonar",
            status_code=429,
        )
        with pytest.raises(ProviderError) as exc:
            await adapter.search("test", options=None)  # type: ignore[arg-type]
        assert exc.value.error_class == "rate_limited"

    async def test_extract_unsupported(self, adapter: PerplexityAdapter):
        with pytest.raises(ProviderError) as exc:
            await adapter.extract("http://example.com", options=None)  # type: ignore[arg-type]
        assert "does not support extraction" in str(exc.value)

    async def test_health_check_ok(self, adapter: PerplexityAdapter, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            method="POST",
            url="https://api.perplexity.ai/v1/sonar",
            status_code=401,
            json={"error": "invalid_api_key"},
        )
        assert await adapter.health_check() is True

    async def test_health_check_fail(self, adapter: PerplexityAdapter, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            method="POST",
            url="https://api.perplexity.ai/v1/sonar",
            status_code=500,
        )
        assert await adapter.health_check() is False  # 500 not in healthy set

    async def test_health_check_network_error(
        self, adapter: PerplexityAdapter, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_exception(ConnectionError("connection refused"))
        assert await adapter.health_check() is False
