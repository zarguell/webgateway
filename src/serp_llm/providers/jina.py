"""Jina Reader content extraction provider adapter.

Jina Reader (https://r.jina.ai/) fetches a URL and returns its content
as clean markdown, HTML, or plain text. It can be used via the managed
endpoint or self-hosted.
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

__all__ = ["JinaReaderAdapter"]


class JinaReaderAdapter:
    """Adapter for the Jina Reader extraction service."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://r.jina.ai",
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
        return "jina"

    @property
    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            name="jina",
            self_hosted=True,
            data_retention_days=None,
            trains_on_queries=None,
            gdpr_compliant=True,
            data_residency=["local"],
            capabilities=["extract"],
        )

    async def search(
        self, query: str, options: SearchOptions
    ) -> SearchResult:
        """Jina Reader does not support web search."""
        raise ProviderError("jina", "Jina Reader does not support search")

    async def extract(
        self, url: str, options: ExtractOptions
    ) -> ExtractResult:
        """Fetch and extract content from *url* via Jina Reader."""
        headers: dict[str, str] = {
            "X-Return-Format": options.format,
            "X-Timeout": str(options.timeout),
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        try:
            async with httpx.AsyncClient(
                proxy=options.proxy_url,
                timeout=options.timeout + 5,
            ) as client:
                resp = await client.get(
                    f"{self._base_url}/{url}", headers=headers
                )
        except httpx.HTTPError as exc:
            raise ProviderError(
                "jina",
                f"Request failed: {exc}",
            ) from exc

        if resp.status_code >= 400:
            raise ProviderError(
                "jina",
                f"HTTP {resp.status_code}",
                status_code=resp.status_code,
            )

        body = resp.text
        title = _extract_title(body)
        content = _strip_title_line(body) if title is not None else body

        return ExtractResult(
            content=content,
            format=options.format,
            url=url,
            title=title,
            status_code=resp.status_code,
        )

    async def health_check(self) -> bool:
        """Check whether the Jina Reader service is reachable."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self._base_url}/https://example.com"
                )
                return resp.status_code < 400
        except httpx.HTTPError:
            return False


def _extract_title(body: str) -> str | None:
    """Extract a ``Title:`` value from the top of a Jina response, if present."""
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("title:"):
            candidate = stripped[len("title:") :].strip()
            return candidate or None
        # Title line, if present, is the first meaningful line.
        if stripped:
            return None
    return None


def _strip_title_line(body: str) -> str:
    """Return *body* with the leading ``Title:`` line removed."""
    lines = body.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.strip().lower().startswith("title:"):
            return "".join(lines[i + 1 :]).lstrip("\n")
    return body
