"""CDP Chrome sidecar adapter — controls host Chrome browser via Chrome DevTools Protocol.

The sidecar wraps the Chrome DevTools Protocol (CDP) to provide simple HTTP
JSON endpoints for page extraction. It manages a single host Chrome instance,
connects via WebSocket CDP, and renders pages to markdown. This adapter only
supports ``extract()`` — it is not a search API.
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

__all__ = ["CdpChromeAdapter"]


class CdpChromeAdapter:
    """Adapter for a CDP Chrome sidecar instance.

    Parameters
    ----------
    config:
        Dictionary with optional keys ``base_url`` (default
        ``http://localhost:9222``) and ``timeout`` (default ``30`` seconds).
    """

    def __init__(self, config: dict | None = None) -> None:
        config = config or {}
        self._base_url = config.get("base_url", "http://localhost:9222").rstrip("/")
        self._timeout = config.get("timeout", 30)

    # ------------------------------------------------------------------
    # ProviderAdapter protocol
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "cdp_chrome"

    @property
    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            name=self.name,
            self_hosted=True,
            data_retention_days=0,
            trains_on_queries=False,
            gdpr_compliant=True,
            data_residency=["local"],
            capabilities=["extract"],
            specialization="cdp_browser",
            cost_units_per_call=0.6,
        )

    async def search(self, query: str, options: SearchOptions) -> SearchResult:
        """CDP Chrome does not support web search."""
        raise ProviderError(self.name, "CDP Chrome does not support search")

    async def extract(self, url: str, options: ExtractOptions) -> ExtractResult:
        """Extract content from *url* via CDP Chrome sidecar.

        Sends a request to the sidecar's ``/extract`` endpoint. The sidecar
        navigates Chrome to the URL, waits for the page to render, and
        converts the content to markdown (or the requested format).
        """
        payload: dict[str, object] = {
            "url": url,
            "timeout": self._timeout,
        }

        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"{self._base_url}/extract",
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise ProviderError(
                self.name, f"Request failed: {exc}"
            ) from exc

        if resp.status_code >= 400:
            error_class: str | None = None
            if resp.status_code == 503:
                error_class = "service_unavailable"
            raise ProviderError(
                self.name,
                f"CDP Chrome returned HTTP {resp.status_code}",
                status_code=resp.status_code,
                error_class=error_class,
            )

        data = resp.json()
        content = str(data.get("content", ""))
        fmt = str(data.get("format", "markdown"))
        final_url = str(data.get("url", url))
        title = data.get("title")

        return ExtractResult(
            content=content,
            format=fmt,
            url=final_url,
            title=title,
        )

    async def health_check(self) -> bool:
        """Check whether the CDP Chrome sidecar is healthy."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self._base_url}/health")
                if resp.status_code >= 400:
                    return False
                data = resp.json()
                return data.get("status") == "ok"
        except httpx.HTTPError:
            return False
