"""Adapter for the invisible_playwright REST sidecar.

The sidecar runs a C++-patched Firefox 150 that is undetectable by
Cloudflare, DataDome, and reCAPTCHA v3 fingerprinting.  This adapter
only supports ``extract()`` — the stealth browser is not a search API.
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


class InvisiblePlaywrightAdapter:
    """Adapter for the invisible_playwright REST sidecar."""

    def __init__(
        self,
        base_url: str = "http://invisible-playwright:3001",
        timeout: int = 15,
        *,
        warnings: list[str] | None = None,
        firefox_version: str = "150",
        cost_units_per_call: float = 0.8,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._warnings = warnings or []
        self._firefox_version = firefox_version
        self._cost_units_per_call = cost_units_per_call

    @property
    def name(self) -> str:
        return "invisible_playwright"

    @property
    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            name=self.name,
            self_hosted=True,
            data_retention_days=0,
            trains_on_queries=False,
            gdpr_compliant=True,
            hipaa_compliant=False,
            data_residency=["local"],
            capabilities=["extract"],
            warnings=list(self._warnings),
            stealth=True,
            engine="firefox",
            firefox_version=self._firefox_version,
            specialization="stealth_primary",
            cost_units_per_call=self._cost_units_per_call,
        )

    async def search(self, query: str, options: SearchOptions) -> SearchResult:
        raise ProviderError(
            self.name,
            "Stealth browser does not support search — use extract() instead",
            error_class="not_supported",
        )

    async def extract(self, url: str, options: ExtractOptions) -> ExtractResult:
        """Scrape *url* via the invisible_playwright sidecar."""
        payload: dict[str, object] = {
            "url": url,
            "timeout": int((options.timeout or self._timeout) * 1000),
        }

        if options.proxy_url:
            payload["proxy"] = options.proxy_url

        if options.fingerprint_id:
            payload["fingerprint"] = options.fingerprint_id
        elif options.session_id:
            payload["fingerprint"] = "rotate"

        if options.session_id:
            payload["session_id"] = options.session_id

        if options.session_cookies:
            payload["cookies"] = [
                {"name": k, "value": v}
                for k, v in options.session_cookies.items()
            ]

        if options.user_agent:
            payload["user_agent"] = options.user_agent

        if options.wait_for_selector:
            payload["wait_for_selector"] = options.wait_for_selector

        try:
            async with httpx.AsyncClient(
                timeout=options.timeout + 30 if options.timeout else self._timeout + 30,
            ) as client:
                resp = await client.post(
                    f"{self._base_url}/scrape",
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise ProviderError(
                self.name, f"Request failed: {exc}"
            ) from exc

        if resp.status_code >= 400:
            raise ProviderError(
                self.name,
                f"HTTP {resp.status_code}",
                status_code=resp.status_code,
            )

        data = resp.json()
        content = str(data.get("content", ""))
        # The IPW sidecar returns raw rendered HTML but labels it "markdown".
        # Override to "html" so the post-processing pipeline runs extraction
        # (trafilatura / readability) and markdown conversion on it.
        fmt = data.get("format", "markdown")
        if fmt == "markdown" and content.strip().startswith("<"):
            fmt = "html"
        return ExtractResult(
            content=content,
            format=fmt,
            url=str(data.get("url", url)),
            title=str(data.get("title")) if data.get("title") else None,
        )

    async def health_check(self) -> bool:
        """Check if the sidecar is reachable via its health endpoint."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self._base_url}/health")
                return resp.status_code < 400
        except httpx.HTTPError:
            return False
