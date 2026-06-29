"""Tavily provider adapter.

Tavily (https://api.tavily.com/) is an agent-optimised search and extraction
API.  It supports both web search (``POST /search``) and URL content
extraction (``POST /extract``).
"""

from __future__ import annotations

import httpx

from serp_llm.providers.base import (
    ExtractOptions,
    ExtractResult,
    ProviderError,
    ProviderMetadata,
    ResultItem,
    SearchOptions,
    SearchResult,
)

__all__ = ["TavilyAdapter"]


class TavilyAdapter:
    """Adapter for the Tavily search and extraction API."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.tavily.com",
        timeout: int = 15,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    # ------------------------------------------------------------------
    # ProviderAdapter protocol
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "tavily"

    @property
    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            name="tavily",
            self_hosted=False,
            data_retention_days=None,
            trains_on_queries=None,
            gdpr_compliant=False,
            data_residency=["US"],
            privacy_policy_url="https://tavily.com/privacy-policy",
            capabilities=["search", "extract"],
        )

    async def search(
        self, query: str, options: SearchOptions
    ) -> SearchResult:
        """Query Tavily and return normalised results."""
        if not self._api_key:
            raise ProviderError("tavily", "API key is required")

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "query": query,
            "max_results": min(options.num_results, 20),
            "search_depth": "basic",
        }

        try:
            async with httpx.AsyncClient(
                proxy=options.proxy_url,
                timeout=options.timeout + 5,
            ) as client:
                resp = await client.post(
                    f"{self._base_url}/search",
                    headers=headers,
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise ProviderError("tavily", f"Request failed: {exc}") from exc

        if resp.status_code >= 400:
            raise ProviderError(
                "tavily",
                f"HTTP {resp.status_code}",
                status_code=resp.status_code,
            )

        data = resp.json()
        raw_results: list[dict[str, object]] = data.get("results", [])

        results: list[ResultItem] = []
        for item in raw_results[: options.num_results]:
            results.append(
                ResultItem(
                    title=str(item.get("title", "")),
                    url=str(item.get("url", "")),
                    snippet=str(item.get("content", "")),
                )
            )

        return SearchResult(results=results)

    async def extract(
        self, url: str, options: ExtractOptions
    ) -> ExtractResult:
        """Extract content from *url* via Tavily's /extract endpoint."""
        if not self._api_key:
            raise ProviderError("tavily", "API key is required")

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "urls": url,
            "extract_depth": "basic",
            "format": "markdown" if options.format == "markdown" else "text",
        }

        try:
            async with httpx.AsyncClient(
                proxy=options.proxy_url,
                timeout=options.timeout + 10,
            ) as client:
                resp = await client.post(
                    f"{self._base_url}/extract",
                    headers=headers,
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise ProviderError("tavily", f"Request failed: {exc}") from exc

        if resp.status_code >= 400:
            raise ProviderError(
                "tavily",
                f"HTTP {resp.status_code}",
                status_code=resp.status_code,
            )

        data = resp.json()
        results_list: list[dict[str, object]] = data.get("results", [])
        if not results_list:
            failed = data.get("failed_results", [])
            if failed:
                raise ProviderError(
                    "tavily",
                    f"Extraction failed: {failed[0].get('error', 'unknown')}",
                )
            raise ProviderError("tavily", "No content returned")

        first = results_list[0]
        content = str(first.get("raw_content", ""))

        return ExtractResult(
            content=content,
            format=options.format,
            url=url,
            status_code=resp.status_code,
        )

    async def health_check(self) -> bool:
        """Check whether the Tavily API is reachable and authenticated."""
        if not self._api_key:
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{self._base_url}/search",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json={"query": "test", "max_results": 1},
                )
                return resp.status_code < 400
        except httpx.HTTPError:
            return False
