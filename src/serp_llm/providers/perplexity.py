"""Perplexity provider adapter — AI-native web search.

Perplexity Sonar API (api.perplexity.ai) is an OpenAI-compatible chat
completion endpoint that returns AI-synthesised answers with citations
and structured search results.

Default model: sonar-pro (best accuracy/cost balance).
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

__all__ = ["PerplexityAdapter"]

_SONAR_ENDPOINT = "/v1/sonar"


class PerplexityAdapter:
    """Adapter for the Perplexity Sonar search API."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "sonar-pro",
        timeout: int = 15,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = "https://api.perplexity.ai"
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "perplexity"

    @property
    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            name="perplexity",
            self_hosted=False,
            data_retention_days=None,
            trains_on_queries=True,
            gdpr_compliant=False,
            hipaa_compliant=False,
            data_residency=["US"],
            privacy_policy_url="https://www.perplexity.ai/privacy",
            mcp_native=False,
            capabilities=["search"],
            specialization="ai_native",
            cost_units_per_call=0.1,
        )

    async def search(
        self, query: str, options: SearchOptions
    ) -> SearchResult:
        if not self._api_key:
            raise ProviderError(self.name, "API key required")

        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": query}],
            "web_search_options": {"search_context_size": "medium"},
            "return_images": False,
            "return_related_questions": False,
            "stream": False,
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}{_SONAR_ENDPOINT}",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if resp.status_code == 429:
                raise ProviderError(
                    self.name,
                    "rate limited",
                    status_code=429,
                    error_class="rate_limited",
                )
            if resp.status_code == 401 or resp.status_code == 403:
                raise ProviderError(
                    self.name,
                    f"authentication failed ({resp.status_code})",
                    status_code=resp.status_code,
                )
            if resp.status_code != 200:
                raise ProviderError(
                    self.name,
                    f"search returned {resp.status_code}",
                    status_code=resp.status_code,
                )

            data = resp.json()

        results: list[ResultItem] = []
        citations = data.get("citations", [])
        search_results = data.get("search_results", [])
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")

        for item in search_results:
            results.append(
                ResultItem(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("snippet", ""),
                    published_date=item.get("date"),
                )
            )

        if not results:
            for i, cite_url in enumerate(citations):
                results.append(
                    ResultItem(
                        title=f"Source {i + 1}",
                        url=cite_url,
                        snippet=content[:200] if i == 0 else "",
                    )
                )

        return SearchResult(results=results)

    async def extract(
        self, url: str, options: ExtractOptions
    ) -> ExtractResult:
        raise ProviderError(self.name, "does not support extraction")

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.post(
                    f"{self._base_url}{_SONAR_ENDPOINT}",
                    headers={
                        "Authorization": f"Bearer {self._api_key or ''}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self._model,
                        "messages": [{"role": "user", "content": "hi"}],
                        "stream": False,
                    },
                )
                return resp.status_code in (200, 401, 403, 422)
        except Exception:
            return False
