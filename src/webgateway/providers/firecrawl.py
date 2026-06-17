"""Firecrawl provider adapter (cloud + self-hosted).

Firecrawl is a full-featured extraction and search API with JS rendering,
structured output, and proxy rotation.

The same adapter class handles both deployment modes:
- **Cloud**: ``https://api.firecrawl.dev`` (API key optional, rate-limited)
- **Self-hosted**: ``http://firecrawl:3002`` (no key, local instance)

When a key is provided it is sent via ``Authorization: Bearer``.
"""

from __future__ import annotations

import httpx

from webgateway.providers.base import (
    ExtractOptions,
    ExtractResult,
    ProviderError,
    ProviderMetadata,
    ResultItem,
    SearchOptions,
    SearchResult,
)

__all__ = ["FirecrawlAdapter"]


class FirecrawlAdapter:
    """Adapter for the Firecrawl API (scrape + search).

    Pass ``self_hosted=True`` for a local Firecrawl instance to get
    accurate provider metadata (local data residency, no retention).
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.firecrawl.dev",
        timeout: int = 15,
        *,
        self_hosted: bool = False,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._self_hosted = self_hosted

    # ------------------------------------------------------------------
    # ProviderAdapter protocol
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "firecrawl_selfhosted" if self._self_hosted else "firecrawl"

    @property
    def metadata(self) -> ProviderMetadata:
        name = self.name
        if self._self_hosted:
            return ProviderMetadata(
                name=name,
                self_hosted=True,
                data_retention_days=0,
                trains_on_queries=False,
                gdpr_compliant=True,
                data_residency=["local"],
                capabilities=["search", "extract"],
            )
        return ProviderMetadata(
            name=name,
            self_hosted=False,
            data_retention_days=None,
            trains_on_queries=False,
            gdpr_compliant=False,
            data_residency=["US"],
            privacy_policy_url="https://www.firecrawl.dev/privacy",
            capabilities=["search", "extract"],
        )

    async def search(
        self, query: str, options: SearchOptions
    ) -> SearchResult:
        """Query Firecrawl search and return normalised results."""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload = {
            "query": query,
            "limit": min(options.num_results, 20),
        }

        try:
            async with httpx.AsyncClient(
                proxy=options.proxy_url,
                timeout=options.timeout + 5,
            ) as client:
                resp = await client.post(
                    f"{self._base_url}/v2/search",
                    headers=headers,
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise ProviderError("firecrawl", f"Request failed: {exc}") from exc

        if resp.status_code >= 400:
            raise ProviderError(
                "firecrawl",
                f"HTTP {resp.status_code}",
                status_code=resp.status_code,
            )

        data = resp.json()
        results_list: list[dict[str, object]] = []

        # Response shape depends on whether scrapeOptions was passed.
        # Without scrapeOptions: data.web[] holds search results.
        # With scrapeOptions: data[] is a flat array with scraped content.
        inner = data.get("data", {})
        if isinstance(inner, dict):
            results_list = inner.get("web", [])
        elif isinstance(inner, list):
            results_list = inner

        results: list[ResultItem] = []
        for item in results_list[: options.num_results]:
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
        """Scrape and extract content from *url* via Firecrawl's /v2/scrape."""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        formats: list[str] = ["html"] if options.format == "html" else ["markdown"]

        payload: dict[str, object] = {
            "url": url,
            "formats": formats,
            "onlyMainContent": True,
        }
        if options.wait_for_selector:
            payload["waitFor"] = 3000

        try:
            async with httpx.AsyncClient(
                proxy=options.proxy_url,
                timeout=options.timeout + 30,
            ) as client:
                resp = await client.post(
                    f"{self._base_url}/v2/scrape",
                    headers=headers,
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise ProviderError("firecrawl", f"Request failed: {exc}") from exc

        if resp.status_code >= 400:
            raise ProviderError(
                "firecrawl",
                f"HTTP {resp.status_code}",
                status_code=resp.status_code,
            )

        data = resp.json()
        if not data.get("success", False):
            raise ProviderError(
                "firecrawl",
                f"Scrape failed: {data.get('error', 'unknown error')}",
            )

        page_data: dict[str, object] = data.get("data", {})
        content_key = "markdown" if "markdown" in page_data else "html"
        content = str(page_data.get(content_key, ""))
        metadata = page_data.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}

        return ExtractResult(
            content=content,
            format="markdown" if content_key == "markdown" else "html",
            url=str(metadata.get("sourceURL", url)),
            title=_coerce_optional_str(metadata.get("title")),
            status_code=int(metadata.get("statusCode", resp.status_code)),
        )

    async def health_check(self) -> bool:
        """Check whether the Firecrawl instance is reachable.

        **Self-hosted** — uses the real ``/health`` endpoint (no credit cost).

        **Cloud** — checks that an API key is configured.  We do **not** make a
        real scrape call because every ``/v2/scrape`` costs one credit against
        the monthly quota.  Actual requests will surface auth/connectivity
        errors naturally.
        """
        if self._self_hosted:
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.get(f"{self._base_url}/health")
                    return resp.status_code < 400
            except httpx.HTTPError:
                return False
        # Cloud — verify credentials are present; skip paid API call.
        return bool(self._api_key)


def _coerce_optional_str(value: object) -> str | None:
    """Return *value* as a str, or None if it is falsy/None."""
    if value is None:
        return None
    text = str(value)
    return text or None
