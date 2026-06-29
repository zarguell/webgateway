"""Context7 provider adapter — library docs search (PRD §19).

Context7 (context7.com) provides versioned library documentation via a
two-phase REST API: first resolve a library name to a Context7 library ID,
then fetch docs snippets for the matched library.

Supports anonymous access (no API key) with lower rate limits.
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

__all__ = ["Context7Adapter"]

_SEARCH_ENDPOINT = "/api/v2/libs/search"
_CONTEXT_ENDPOINT = "/api/v2/context"
_SKIP_WORDS = frozenset(
    {
        "the", "a", "an", "how", "what", "why", "when", "for",
        "in", "to", "of", "is", "do", "does", "can", "i", "we",
        "you", "use", "get", "set", "make", "with", "from",
    }
)


class Context7Adapter:
    """Adapter for Context7 documentation search."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://context7.com",
        timeout: int = 15,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "context7"

    @property
    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            name="context7",
            self_hosted=False,
            data_retention_days=None,
            trains_on_queries=False,
            gdpr_compliant=True,
            hipaa_compliant=False,
            data_residency=["US", "EU"],
            privacy_policy_url="https://context7.com/privacy",
            mcp_native=True,
            capabilities=["search"],
            specialization="docs",
            cost_units_per_call=0.0,
        )

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    @staticmethod
    def _guess_library_name(query: str) -> str:
        words = query.strip().split()
        for w in words:
            cleaned = w.strip("(),./\\")
            if cleaned and cleaned[0].isupper() and cleaned.lower() not in _SKIP_WORDS:
                return cleaned
        return words[0] if words else ""

    async def search(
        self, query: str, options: SearchOptions
    ) -> SearchResult:
        library_name = self._guess_library_name(query)
        if not library_name:
            return SearchResult()

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            lib_resp = await client.get(
                f"{self._base_url}{_SEARCH_ENDPOINT}",
                headers=self._headers(),
                params={"libraryName": library_name, "query": query},
            )
            if lib_resp.status_code != 200:
                raise ProviderError(
                    self.name,
                    f"libs/search returned {lib_resp.status_code}",
                    status_code=lib_resp.status_code,
                )

            lib_data = lib_resp.json()
            libraries = lib_data.get("results", [])
            if not libraries:
                return SearchResult()

            library_id = libraries[0]["id"]
            ctx_resp = await client.get(
                f"{self._base_url}{_CONTEXT_ENDPOINT}",
                headers=self._headers(),
                params={"libraryId": library_id, "query": query, "type": "json"},
            )
            if ctx_resp.status_code != 200:
                return SearchResult()

            ctx_data = ctx_resp.json()

        results: list[ResultItem] = []

        for snippet in ctx_data.get("codeSnippets", []):
            code_title = snippet.get("codeTitle", "") or snippet.get("pageTitle", "")
            code_text = ""
            for entry in snippet.get("codeList", []):
                code_text += f"\n```{entry.get('language', '')}\n{entry.get('code', '')}\n```\n"
            url = snippet.get("codeId", "")
            if url:
                url = url.split("#")[0]
            results.append(
                ResultItem(
                    title=code_title,
                    url=url,
                    snippet=snippet.get("codeDescription", "") + code_text,
                )
            )

        for snippet in ctx_data.get("infoSnippets", []):
            content = snippet.get("content", "")[:300]
            results.append(
                ResultItem(
                    title=snippet.get("breadcrumb", "") or snippet.get("pageId", ""),
                    url=snippet.get("pageId", ""),
                    snippet=content,
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
                resp = await client.get(
                    f"{self._base_url}{_SEARCH_ENDPOINT}",
                    headers=self._headers(),
                    params={"libraryName": "test", "query": "test"},
                )
                return resp.status_code < 500
        except httpx.HTTPError:
            return False
