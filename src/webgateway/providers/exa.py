"""Exa provider adapter — neural semantic search (PRD-addendum §18-19).

Exa (exa.ai) is a neural search engine that understands semantic meaning
rather than just keywords. It supports type: auto/neural search and
separate content extraction via /contents.

Note on contents: /search returns results without page text unless the
``contents`` sub-object is provided.  This adapter requests
``highlights`` + ``text`` by default.
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

__all__ = ["ExaAdapter"]


class ExaAdapter:
    """Adapter for the Exa semantic search API."""

    def __init__(
        self,
        api_key: str | None = None,
        timeout: int = 15,
    ) -> None:
        self._api_key = api_key
        self._base_url = "https://api.exa.ai"
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "exa"

    @property
    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            name="exa",
            self_hosted=False,
            data_retention_days=None,
            trains_on_queries=True,
            gdpr_compliant=False,
            hipaa_compliant=False,
            data_residency=["US"],
            privacy_policy_url="https://exa.ai/privacy",
            mcp_native=False,
            capabilities=["search", "extract"],
            specialization="semantic",
            cost_units_per_call=1.0,
        )

    async def search(
        self, query: str, options: SearchOptions
    ) -> SearchResult:
        if not self._api_key:
            raise ProviderError(self.name, "API key required")

        num_results = options.num_results if options else 10
        payload = {
            "query": query,
            "numResults": num_results,
            "type": "auto",
            "contents": {
                "highlights": True,
                "text": {"maxCharacters": 2000},
            },
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/search",
                headers=self._headers(),
                json=payload,
            )
            if resp.status_code == 429:
                raise ProviderError(
                    self.name,
                    "rate limited",
                    status_code=429,
                    error_class="rate_limited",
                )
            if resp.status_code == 402:
                raise ProviderError(
                    self.name,
                    "credits exhausted",
                    status_code=402,
                    error_class="credits_exhausted",
                )
            if resp.status_code == 401:
                raise ProviderError(
                    self.name,
                    "invalid API key",
                    status_code=401,
                )
            if resp.status_code != 200:
                raise ProviderError(
                    self.name,
                    f"search returned {resp.status_code}",
                    status_code=resp.status_code,
                )

            data = resp.json()

        results: list[ResultItem] = []
        for item in data.get("results", []):
            highlights = item.get("highlights", [])
            snippet = highlights[0] if highlights else ""
            if not snippet:
                text = item.get("text", "")
                snippet = text[:300] if text else ""
            results.append(
                ResultItem(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=snippet,
                    published_date=item.get("publishedDate"),
                )
            )

        return SearchResult(results=results)

    async def extract(
        self, url: str, options: ExtractOptions
    ) -> ExtractResult:
        if not self._api_key:
            raise ProviderError(self.name, "API key required")

        payload = {
            "urls": [url],
            "text": {"maxCharacters": 10000},
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/contents",
                headers=self._headers(),
                json=payload,
            )
            if resp.status_code != 200:
                raise ProviderError(
                    self.name,
                    f"contents returned {resp.status_code}",
                    status_code=resp.status_code,
                )

            data = resp.json()

        results = data.get("results", [])
        if not results:
            return ExtractResult(url=url, content="")

        return ExtractResult(
            content=results[0].get("text", ""),
            format="markdown",
            url=url,
            title=results[0].get("title"),
        )

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._api_key or "",
            "Content-Type": "application/json",
        }

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.post(
                    f"{self._base_url}/search",
                    headers={
                        "x-api-key": self._api_key or "",
                        "Content-Type": "application/json",
                    },
                    json={
                        "query": "test",
                        "numResults": 1,
                        "type": "auto",
                    },
                )
                return resp.status_code in (200, 401, 402, 429)
        except Exception:
            return False
