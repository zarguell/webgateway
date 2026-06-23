"""Zyte provider adapter (cloud extraction API).

Zyte (https://www.zyte.com/) is a cloud extraction service with managed proxy
rotation and headless-browser rendering.  This adapter uses the ``/v1/extract``
endpoint with ``browserHtml`` to retrieve fully rendered page HTML.

Authentication is HTTP Basic: the API key is the username, the password is empty.

The adapter only supports extraction — Zyte does not provide a search API.
"""

from __future__ import annotations

import base64

import httpx

from webgateway.providers.base import (
    ExtractOptions,
    ExtractResult,
    ProviderError,
    ProviderMetadata,
    SearchOptions,
    SearchResult,
)

__all__ = ["ZyteAdapter"]


class ZyteAdapter:
    """Adapter for the Zyte cloud extraction API.

    Uses HTTP Basic Auth (api_key as username, empty password) to call the
    ``/v1/extract`` endpoint with ``browserHtml`` for rendered page content.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.zyte.com",
        timeout: int = 120,
        geolocation: str | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._geolocation = geolocation

    # ------------------------------------------------------------------
    # ProviderAdapter protocol
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "zyte"

    @property
    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            name="zyte",
            self_hosted=False,
            data_retention_days=None,
            trains_on_queries=False,
            gdpr_compliant=False,
            data_residency=["unknown"],
            privacy_policy_url="https://www.zyte.com/privacy-policy/",
            capabilities=["extract"],
        )

    async def search(
        self, query: str, options: SearchOptions
    ) -> SearchResult:
        """Zyte does not support search."""
        raise ProviderError("zyte", "Zyte does not support search")

    async def extract(
        self, url: str, options: ExtractOptions
    ) -> ExtractResult:
        """Extract rendered HTML content from *url* via Zyte's /v1/extract."""
        if not self._api_key:
            raise ProviderError("zyte", "API key is required")

        credentials = base64.b64encode(f"{self._api_key}:".encode()).decode()
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Authorization": f"Basic {credentials}",
            "Accept-Encoding": "gzip, deflate",
        }

        payload: dict[str, object] = {
            "url": url,
            "browserHtml": True,
        }
        if self._geolocation:
            payload["geolocation"] = self._geolocation

        try:
            async with httpx.AsyncClient(
                proxy=options.proxy_url,
                timeout=self._timeout,
            ) as client:
                resp = await client.post(
                    f"{self._base_url}/v1/extract",
                    headers=headers,
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise ProviderError("zyte", f"Request failed: {exc}") from exc

        status = resp.status_code

        if status in (401, 403):
            raise ProviderError(
                "zyte",
                "Zyte authentication failed",
                status_code=status,
            )
        if status == 429:
            raise ProviderError(
                "zyte",
                "Zyte rate limit exceeded",
                status_code=status,
            )
        if status == 520:
            raise ProviderError(
                "zyte",
                "Zyte ban detected",
                status_code=status,
            )
        if status >= 500:
            raise ProviderError(
                "zyte",
                "Zyte server error",
                status_code=status,
            )
        if status >= 400:
            raise ProviderError(
                "zyte",
                f"HTTP {status}",
                status_code=status,
            )

        data = resp.json()
        html = str(data.get("browserHtml", ""))
        final_url = str(data.get("url", url))

        return ExtractResult(
            content=html,
            format="html",
            url=final_url,
            status_code=status,
        )

    async def health_check(self) -> bool:
        """Verify credentials are present without making a paid API call."""
        return bool(self._api_key)
