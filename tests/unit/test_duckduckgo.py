from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from serp_llm.providers.base import ExtractOptions, ProviderError, SearchOptions
from serp_llm.providers.duckduckgo import DuckDuckGoAdapter


@pytest.fixture
def adapter() -> DuckDuckGoAdapter:
    return DuckDuckGoAdapter(timeout=15)


_FAKE_RESULTS = [
    {"title": "Python Programming", "href": "https://python.org", "body": "Official site"},
    {"title": "FastAPI Docs", "href": "https://fastapi.tiangolo.com", "body": "Modern framework"},
]


class TestDuckDuckGoAdapter:
    async def test_name(self, adapter: DuckDuckGoAdapter):
        assert adapter.name == "duckduckgo"

    async def test_metadata(self, adapter: DuckDuckGoAdapter):
        meta = adapter.metadata
        assert meta.name == "duckduckgo"
        assert "search" in meta.capabilities
        assert meta.self_hosted is False
        assert meta.gdpr_compliant is True

    @patch("serp_llm.providers.duckduckgo.DDGS")
    async def test_search_success(self, mock_ddgs_cls: MagicMock, adapter: DuckDuckGoAdapter):
        mock_instance = mock_ddgs_cls.return_value
        mock_instance.text.return_value = _FAKE_RESULTS

        options = SearchOptions(num_results=5, timeout=10)
        result = await adapter.search("python", options)

        assert len(result.results) == 2
        assert result.results[0].title == "Python Programming"
        assert result.results[0].url == "https://python.org"
        assert result.results[0].snippet == "Official site"
        mock_instance.text.assert_called_once_with("python", max_results=5)

    @patch("serp_llm.providers.duckduckgo.DDGS")
    async def test_search_passes_proxy(self, mock_ddgs_cls: MagicMock, adapter: DuckDuckGoAdapter):
        mock_instance = mock_ddgs_cls.return_value
        mock_instance.text.return_value = []

        options = SearchOptions(num_results=1, proxy_url="http://proxy:3128", timeout=10)
        await adapter.search("test", options)

        mock_ddgs_cls.assert_called_once_with(
            proxy="http://proxy:3128", timeout=10
        )

    @patch("serp_llm.providers.duckduckgo.DDGS")
    async def test_search_empty_results(self, mock_ddgs_cls: MagicMock, adapter: DuckDuckGoAdapter):
        mock_instance = mock_ddgs_cls.return_value
        mock_instance.text.return_value = []

        options = SearchOptions(num_results=5, timeout=10)
        result = await adapter.search("obscure query", options)

        assert len(result.results) == 0

    @patch("serp_llm.providers.duckduckgo.DDGS")
    async def test_search_rate_limit(self, mock_ddgs_cls: MagicMock, adapter: DuckDuckGoAdapter):
        from ddgs.exceptions import RatelimitException

        mock_instance = mock_ddgs_cls.return_value
        mock_instance.text.side_effect = RatelimitException("Rate limited")

        options = SearchOptions(num_results=1, timeout=10)
        with pytest.raises(ProviderError) as exc_info:
            await adapter.search("test", options)
        assert exc_info.value.error_class == "rate_limited"

    @patch("serp_llm.providers.duckduckgo.DDGS")
    async def test_search_timeout(self, mock_ddgs_cls: MagicMock, adapter: DuckDuckGoAdapter):
        from ddgs.exceptions import TimeoutException

        mock_instance = mock_ddgs_cls.return_value
        mock_instance.text.side_effect = TimeoutException("Timed out")

        options = SearchOptions(num_results=1, timeout=10)
        with pytest.raises(ProviderError) as exc_info:
            await adapter.search("test", options)
        assert exc_info.value.error_class == "timeout"

    @patch("serp_llm.providers.duckduckgo.DDGS")
    async def test_search_generic_error(self, mock_ddgs_cls: MagicMock, adapter: DuckDuckGoAdapter):
        mock_instance = mock_ddgs_cls.return_value
        mock_instance.text.side_effect = RuntimeError("Network error")

        options = SearchOptions(num_results=1, timeout=10)
        with pytest.raises(ProviderError) as exc_info:
            await adapter.search("test", options)
        assert "Search failed" in str(exc_info.value)
        assert exc_info.value.error_class is None

    async def test_extract_not_supported(self, adapter: DuckDuckGoAdapter):
        with pytest.raises(ProviderError, match="does not support extraction"):
            await adapter.extract("https://example.com", ExtractOptions())

    @patch("serp_llm.providers.duckduckgo.DDGS")
    async def test_health_check_success(self, mock_ddgs_cls: MagicMock, adapter: DuckDuckGoAdapter):
        mock_instance = mock_ddgs_cls.return_value
        mock_instance.text.return_value = [{"title": "ok", "href": "http://x.com", "body": "ok"}]

        assert await adapter.health_check() is True

    @patch("serp_llm.providers.duckduckgo.DDGS")
    async def test_health_check_failure(self, mock_ddgs_cls: MagicMock, adapter: DuckDuckGoAdapter):
        mock_instance = mock_ddgs_cls.return_value
        mock_instance.text.side_effect = Exception("connection refused")

        assert await adapter.health_check() is False

    @patch("serp_llm.providers.duckduckgo.DDGS")
    async def test_search_num_results_truncation(
        self, mock_ddgs_cls: MagicMock, adapter: DuckDuckGoAdapter
    ):
        mock_instance = mock_ddgs_cls.return_value
        mock_instance.text.return_value = _FAKE_RESULTS

        options = SearchOptions(num_results=1, timeout=10)
        await adapter.search("python", options)

        mock_instance.text.assert_called_once_with("python", max_results=1)
