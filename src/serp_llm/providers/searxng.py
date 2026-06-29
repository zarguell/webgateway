"""SearXNG meta-search provider adapter.

SearXNG is a privacy-respecting meta-search engine that can be self-hosted.
It aggregates results from multiple search engines and exposes a JSON API
when called with ``format=json``.
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

__all__ = ["SearXNGAdapter"]


class SearXNGAdapter:
    """Adapter for a self-hosted SearXNG instance's JSON search API."""

    def __init__(self, base_url: str, timeout: int = 15) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    # ------------------------------------------------------------------
    # ProviderAdapter protocol
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "searxng"

    @property
    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            name="searxng",
            self_hosted=True,
            data_retention_days=0,
            trains_on_queries=False,
            gdpr_compliant=True,
            data_residency=["local"],
            capabilities=["search"],
        )

    async def search(
        self, query: str, options: SearchOptions
    ) -> SearchResult:
        """Query SearXNG and return normalised search results."""
        params = {
            "q": query,
            "format": "json",
            "pageno": 1,
        }
        try:
            async with httpx.AsyncClient(
                proxy=options.proxy_url,
                timeout=options.timeout,
            ) as client:
                resp = await client.get(
                    f"{self._base_url}/search",
                    params=params,
                    headers={"X-Forwarded-For": "127.0.0.1"},
                )
        except httpx.HTTPError as exc:
            raise ProviderError(
                "searxng",
                f"Request failed: {exc}",
            ) from exc

        if resp.status_code >= 400:
            raise ProviderError(
                "searxng",
                f"SearXNG returned HTTP {resp.status_code}",
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
                    published_date=_coerce_optional_str(
                        item.get("publishedDate")
                    ),
                )
            )

        return SearchResult(results=results)

    async def extract(
        self, url: str, options: ExtractOptions
    ) -> ExtractResult:
        """SearXNG does not support content extraction."""
        raise ProviderError("searxng", "SearXNG does not support extraction")

    async def health_check(self) -> bool:
        """Check whether the SearXNG instance is reachable."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                try:
                    resp = await client.get(f"{self._base_url}/healthz")
                except httpx.HTTPError:
                    resp = await client.get(f"{self._base_url}/")
                return resp.status_code < 400
        except httpx.HTTPError:
            return False


def _coerce_optional_str(value: object) -> str | None:
    """Return *value* as a str, or None if it is falsy/None."""
    if value is None:
        return None
    text = str(value)
    return text or None
