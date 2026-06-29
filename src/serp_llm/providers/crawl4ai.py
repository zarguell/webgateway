"""Crawl4AI content extraction provider adapter.

Crawl4AI (https://github.com/unclecode/crawl4ai) is an open-source
self-hosted crawler with a built-in FastAPI server. It runs as a sidecar
container and exposes two extraction endpoints:

- ``POST /crawl`` — full browser crawl with JS rendering. Returns
  markdown, cleaned HTML, extracted JSON, and metadata. Slower but handles
  JS-heavy sites.
- ``POST /md`` — lightweight markdown extraction. Faster for simple
  pages, no full browser rendering.

The same adapter class handles both modes via the ``mode`` parameter.
It is registered twice in the provider registry — once as ``crawl4ai``
(mode="crawl") and once as ``crawl4ai_md`` (mode="md") — so users can
compose them independently through policy rules and fallback chains.
"""

from __future__ import annotations

import httpx

from serp_llm.providers.base import (
    ExtractOptions,
    ExtractResult,
    ProviderError,
    ProviderMetadata,
    SearchOptions,
    SearchResult,
)

__all__ = ["Crawl4AIAdapter"]


class Crawl4AIAdapter:
    """Adapter for a self-hosted Crawl4AI instance.

    Parameters
    ----------
    base_url:
        URL of the Crawl4AI server (default ``http://crawl4ai:11235``).
    timeout:
        Default timeout in seconds for extraction requests.
    mode:
        ``"crawl"`` uses ``POST /crawl`` (full browser rendering).
        ``"md"`` uses ``POST /md`` (lightweight markdown).
    api_token:
        Optional bearer token for Crawl4AI auth (only needed when
        bound to ``0.0.0.0``).
    """

    def __init__(
        self,
        base_url: str = "http://crawl4ai:11235",
        timeout: int = 30,
        *,
        mode: str = "crawl",
        api_token: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._mode = mode
        self._api_token = api_token

    # ------------------------------------------------------------------
    # ProviderAdapter protocol
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "crawl4ai_md" if self._mode == "md" else "crawl4ai"

    @property
    def metadata(self) -> ProviderMetadata:
        name = self.name
        if self._mode == "md":
            return ProviderMetadata(
                name=name,
                self_hosted=True,
                data_retention_days=0,
                trains_on_queries=False,
                gdpr_compliant=True,
                data_residency=["local"],
                capabilities=["extract"],
                specialization="markdown",
                cost_units_per_call=0.3,
            )
        return ProviderMetadata(
            name=name,
            self_hosted=True,
            data_retention_days=0,
            trains_on_queries=False,
            gdpr_compliant=True,
            data_residency=["local"],
            capabilities=["extract"],
            specialization="browser",
            cost_units_per_call=0.5,
        )

    async def search(
        self, query: str, options: SearchOptions
    ) -> SearchResult:
        """Crawl4AI does not support web search."""
        raise ProviderError(self.name, "Crawl4AI does not support search")

    async def extract(
        self, url: str, options: ExtractOptions
    ) -> ExtractResult:
        """Extract content from *url* via Crawl4AI.

        Dispatches to ``POST /crawl`` (mode="crawl") or ``POST /md``
        (mode="md") depending on the adapter mode.
        """
        if self._mode == "md":
            return await self._extract_md(url, options)
        return await self._extract_crawl(url, options)

    async def health_check(self) -> bool:
        """Check whether the Crawl4AI server is reachable."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self._base_url}/health")
                return resp.status_code < 400
        except httpx.HTTPError:
            return False

    # ------------------------------------------------------------------
    # Internal: /crawl endpoint (full browser crawl)
    # ------------------------------------------------------------------

    async def _extract_crawl(
        self, url: str, options: ExtractOptions
    ) -> ExtractResult:
        headers = _build_headers(self._api_token)
        payload = {
            "urls": [url],
            "crawler_config": {
                "cache_mode": "bypass",
                "word_count_threshold": 10,
            },
        }
        try:
            async with httpx.AsyncClient(
                proxy=options.proxy_url,
                timeout=self._timeout + 30,
            ) as client:
                resp = await client.post(
                    f"{self._base_url}/crawl",
                    headers=headers,
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise ProviderError(
                self.name, f"Request failed: {exc}"
            ) from exc

        if resp.status_code >= 400:
            raise ProviderError(
                self.name,
                f"Crawl4AI returned HTTP {resp.status_code}",
                status_code=resp.status_code,
            )

        data = resp.json()
        results = data.get("results", [])
        if not results:
            raise ProviderError(
                self.name, "Crawl4AI returned empty results"
            )

        result = results[0]
        if not result.get("success", False):
            raise ProviderError(
                self.name,
                f"Crawl failed: {result.get('error_message', 'unknown error')}",
            )

        metadata = result.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}

        # Prefer fit_markdown (cleaner), fall back to markdown.
        content = _coerce_str(
            result.get("fit_markdown") or result.get("markdown") or ""
        )
        title = _coerce_optional_str(metadata.get("title"))

        return ExtractResult(
            content=content,
            format="markdown",
            url=_coerce_str(result.get("url", url)),
            title=title,
            status_code=int(result.get("status_code", resp.status_code)),
        )

    # ------------------------------------------------------------------
    # Internal: /md endpoint (lightweight markdown)
    # ------------------------------------------------------------------

    async def _extract_md(
        self, url: str, options: ExtractOptions
    ) -> ExtractResult:
        headers = _build_headers(self._api_token)
        # /md uses singular "url" and a "f" (filter) param, not "urls".
        payload: dict[str, object] = {"url": url, "f": "fit"}
        try:
            async with httpx.AsyncClient(
                proxy=options.proxy_url,
                timeout=self._timeout + 10,
            ) as client:
                resp = await client.post(
                    f"{self._base_url}/md",
                    headers=headers,
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise ProviderError(
                self.name, f"Request failed: {exc}"
            ) from exc

        if resp.status_code >= 400:
            raise ProviderError(
                self.name,
                f"Crawl4AI returned HTTP {resp.status_code}",
                status_code=resp.status_code,
            )

        # /md returns a flat response, not a results[] array.
        data = resp.json()
        if not data.get("success", False):
            raise ProviderError(
                self.name,
                f"Markdown extraction failed: "
                f"{data.get('error_message', 'unknown error')}",
            )

        content = _coerce_str(data.get("markdown") or "")
        # /md doesn't return a metadata dict with title.
        title = None

        return ExtractResult(
            content=content,
            format="markdown",
            url=_coerce_str(data.get("url", url)),
            title=title,
            status_code=resp.status_code,
        )


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _build_headers(api_token: str | None) -> dict[str, str]:
    """Return common request headers."""
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_token:
        headers["Authorization"] = f"Bearer {api_token}"
    return headers


def _coerce_str(value: object) -> str:
    """Return *value* as a str."""
    return str(value) if value else ""


def _coerce_optional_str(value: object) -> str | None:
    """Return *value* as a str, or None if it is falsy/None."""
    if value is None:
        return None
    text = str(value)
    return text or None
