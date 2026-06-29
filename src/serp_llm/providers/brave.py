"""Brave Search provider adapter.

Brave Search API (https://api.search.brave.com/) is a privacy-respecting
web search API.  This adapter supports **search only** — Brave does not
offer a dedicated page-extraction / scraping endpoint.
"""

from __future__ import annotations

import asyncio

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

__all__ = ["BraveSearchAdapter"]


class BraveSearchAdapter:
    """Adapter for the Brave Search cloud API."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.search.brave.com",
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
        return "brave"

    @property
    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            name="brave",
            self_hosted=False,
            data_retention_days=None,
            trains_on_queries=False,
            gdpr_compliant=False,
            data_residency=["US"],
            privacy_policy_url="https://brave.com/privacy/browser/",
            capabilities=["search"],
        )

    async def search(
        self, query: str, options: SearchOptions
    ) -> SearchResult:
        """Query Brave Search and return normalised results.

        Brave enforces a 1 req/sec rate limit. On 429 we wait 1.5s and
        retry once before giving up.
        """
        if not self._api_key:
            raise ProviderError("brave", "API key is required")

        headers = {
            "X-Subscription-Token": self._api_key,
            "Accept": "application/json",
        }
        params = {
            "q": query,
            "count": min(options.num_results, 20),
        }

        resp = await self._request_with_retry(
            f"{self._base_url}/res/v1/web/search",
            headers=headers,
            params=params,
            proxy_url=options.proxy_url,
            timeout=options.timeout + 5,
        )

        data = resp.json()
        raw_results: list[dict[str, object]] = data.get("web", {}).get(
            "results", []
        )

        results: list[ResultItem] = []
        for item in raw_results[: options.num_results]:
            results.append(
                ResultItem(
                    title=str(item.get("title", "")),
                    url=str(item.get("url", "")),
                    snippet=str(item.get("description", "")),
                )
            )

        return SearchResult(results=results)

    async def extract(
        self, url: str, options: ExtractOptions
    ) -> ExtractResult:
        """Brave does not support content extraction."""
        raise ProviderError("brave", "Brave Search does not support extraction")

    async def _request_with_retry(
        self,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, object],
        proxy_url: str | None = None,
        timeout: int = 20,
        max_retries: int = 1,
    ) -> httpx.Response:
        """GET with retry on 429 (Brave's 1 req/sec rate limit)."""
        for attempt in range(max_retries + 1):
            try:
                async with httpx.AsyncClient(
                    proxy=proxy_url, timeout=timeout
                ) as client:
                    resp = await client.get(
                        url, headers=headers, params=params
                    )
            except httpx.HTTPError as exc:
                raise ProviderError("brave", f"Request failed: {exc}") from exc

            if resp.status_code == 429 and attempt < max_retries:
                await asyncio.sleep(1.5)
                continue

            if resp.status_code >= 400:
                raise ProviderError(
                    "brave",
                    f"HTTP {resp.status_code}",
                    status_code=resp.status_code,
                )
            return resp

        return resp  # unreachable, but satisfies type checker

    async def health_check(self) -> bool:
        """Check whether the Brave Search API is reachable and authenticated."""
        if not self._api_key:
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self._base_url}/res/v1/web/search",
                    headers={
                        "X-Subscription-Token": self._api_key,
                        "Accept": "application/json",
                    },
                    params={"q": "test", "count": 1},
                )
                return resp.status_code < 400
        except httpx.HTTPError:
            return False
