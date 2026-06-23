"""FlareSolverr provider adapter — solves Cloudflare challenges via headless browser.

FlareSolverr (https://github.com/FlareSolverr/FlareSolverr) is a self-hosted
proxy server that bypasses Cloudflare and DDoS-GUARD protection using a
headless Chromium/Firefox browser. It exposes a simple JSON API at ``/v1``
that accepts a ``request.get`` command, solves the challenge, and returns
the rendered page HTML.

This adapter only supports ``extract()`` — FlareSolverr is not a search API.
All errors from FlareSolverr arrive as HTTP 500; the JSON ``status`` field
distinguishes success from failure. ``solution.status`` is always 200
(Selenium limitation) and ``solution.headers`` is always empty, so neither
is used.
"""

from __future__ import annotations

import httpx

from webgateway.providers.base import (
    ExtractOptions,
    ExtractResult,
    ProviderError,
    ProviderMetadata,
    SearchOptions,
    SearchResult,
)

__all__ = ["FlareSolverrAdapter"]


class FlareSolverrAdapter:
    """Adapter for a self-hosted FlareSolverr instance.

    Parameters
    ----------
    config:
        Dictionary with optional keys ``base_url`` (default
        ``http://localhost:8191``) and ``max_timeout`` (default ``60000``
        milliseconds).
    """

    def __init__(self, config: dict | None = None) -> None:
        config = config or {}
        self._base_url = config.get("base_url", "http://localhost:8191").rstrip("/")
        self._max_timeout = config.get("max_timeout", 60000)

    # ------------------------------------------------------------------
    # ProviderAdapter protocol
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "flaresolverr"

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
            specialization="cloudflare_bypass",
            cost_units_per_call=0.5,
        )

    async def search(self, query: str, options: SearchOptions) -> SearchResult:
        """FlareSolverr does not support web search."""
        raise ProviderError(self.name, "FlareSolverr does not support search")

    async def extract(self, url: str, options: ExtractOptions) -> ExtractResult:
        """Extract content from *url* via FlareSolverr.

        Sends a ``request.get`` command. FlareSolverr solves any Cloudflare
        challenge, then returns the rendered page HTML. The raw HTML is
        returned — the post-processing pipeline handles conversion.
        """
        payload: dict[str, object] = {
            "cmd": "request.get",
            "url": url,
            "maxTimeout": self._max_timeout,
        }

        if options.proxy_url:
            payload["proxy"] = {"url": options.proxy_url}

        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"{self._base_url}/v1",
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise ProviderError(
                self.name, f"Request failed: {exc}"
            ) from exc

        if resp.status_code >= 400:
            raise ProviderError(
                self.name,
                f"FlareSolverr returned HTTP {resp.status_code}",
                status_code=resp.status_code,
            )

        data = resp.json()
        status = data.get("status")

        if status != "ok":
            message = data.get("message", "unknown error")
            raise ProviderError(
                self.name,
                f"FlareSolverr challenge failed: {message}",
            )

        solution = data.get("solution") or {}
        html = str(solution.get("response", ""))
        final_url = str(solution.get("url", url))
        cookies = solution.get("cookies")

        return ExtractResult(
            content=html,
            format="html",
            url=final_url,
            cookies=cookies,
        )

    async def health_check(self) -> bool:
        """Check whether the FlareSolverr server is healthy."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self._base_url}/health")
                if resp.status_code >= 400:
                    return False
                data = resp.json()
                return data.get("status") == "ok"
        except httpx.HTTPError:
            return False
