"""DuckDuckGo search provider adapter.

Uses the ``ddgs`` package (https://github.com/deedy5/ddgs) to query
DuckDuckGo's lite interface.  No API key is required, but DuckDuckGo
rate-limits aggressive querying, making this provider best suited as a
zero-cost fallback.

The underlying library is synchronous, so all calls are wrapped in
``asyncio.to_thread`` to avoid blocking the event loop.
"""

from __future__ import annotations

import asyncio

from ddgs import DDGS
from ddgs.exceptions import RatelimitException, TimeoutException

from serp_llm.providers.base import (
    ExtractOptions,
    ExtractResult,
    ProviderError,
    ProviderMetadata,
    ResultItem,
    SearchOptions,
    SearchResult,
)

__all__ = ["DuckDuckGoAdapter"]


class DuckDuckGoAdapter:
    """Adapter for DuckDuckGo search via the ``ddgs`` package."""

    def __init__(self, timeout: int = 15) -> None:
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "duckduckgo"

    @property
    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            name="duckduckgo",
            self_hosted=False,
            data_retention_days=0,
            trains_on_queries=False,
            gdpr_compliant=True,
            data_residency=["us"],
            capabilities=["search"],
        )

    async def search(
        self, query: str, options: SearchOptions
    ) -> SearchResult:
        def _sync_search() -> list[dict[str, str]]:
            proxy = options.proxy_url
            ddgs = DDGS(proxy=proxy, timeout=options.timeout)
            return ddgs.text(query, max_results=options.num_results)

        try:
            raw_results = await asyncio.to_thread(_sync_search)
        except RatelimitException as exc:
            raise ProviderError(
                "duckduckgo",
                f"Rate limited: {exc}",
                error_class="rate_limited",
            ) from exc
        except TimeoutException as exc:
            raise ProviderError(
                "duckduckgo",
                f"Timeout: {exc}",
                error_class="timeout",
            ) from exc
        except Exception as exc:
            raise ProviderError(
                "duckduckgo",
                f"Search failed: {exc}",
            ) from exc

        results: list[ResultItem] = []
        for item in raw_results:
            results.append(
                ResultItem(
                    title=item.get("title", ""),
                    url=item.get("href", ""),
                    snippet=item.get("body", ""),
                )
            )

        return SearchResult(results=results)

    async def extract(
        self, url: str, options: ExtractOptions
    ) -> ExtractResult:
        raise ProviderError("duckduckgo", "DuckDuckGo does not support extraction")

    async def health_check(self) -> bool:
        try:
            await asyncio.to_thread(
                lambda: DDGS(timeout=5).text("health check test", max_results=1)
            )
            return True
        except Exception:
            return False
