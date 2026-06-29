"""DevDocs provider adapter — self-hosted documentation search (PRD §19).

DevDocs (devdocs.io / github.com/freeCodeCamp/devdocs) is an open-source
documentation browser. When self-hosted via Docker it exposes a Sinatra
app on port 9292.

IMPORTANT: DevDocs has NO server-side search API — search is 100 %
client-side JavaScript. This adapter works by:
1. Loading the docs manifest (``GET /docs.json``) to discover doc sets
2. Loading the entry index (``GET /docs/{slug}/index.json``) for relevant
   doc sets
3. Performing server-side string matching on entry names/types
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

__all__ = ["DevDocsAdapter"]

# Fallback doc sets searched when the query doesn't match any doc slug.
_FALLBACK_SLUGS = [
    "javascript",
    "python~3.14",
    "python~3.13",
    "typescript~5.8",
    "typescript~5.7",
    "react~19",
    "react~18",
    "node~22",
    "node~20",
    "rust",
    "go",
]


class DevDocsAdapter:
    """Adapter for self-hosted DevDocs search."""

    def __init__(
        self,
        base_url: str = "http://devdocs:9292",
        timeout: int = 15,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "devdocs"

    @property
    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            name="devdocs",
            self_hosted=True,
            data_retention_days=None,
            trains_on_queries=False,
            gdpr_compliant=True,
            hipaa_compliant=True,
            data_residency=["local"],
            privacy_policy_url=None,
            mcp_native=False,
            capabilities=["search"],
            specialization="docs",
            cost_units_per_call=0.0,
        )

    async def search(
        self, query: str, options: SearchOptions
    ) -> SearchResult:
        num_results = options.num_results if options else 10
        q = query.lower().strip()
        if not q:
            return SearchResult()

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            manifest = await self._load_manifest(client)
            if manifest is None:
                return SearchResult()

            slugs = self._find_relevant_slugs(q, manifest)
            if not slugs:
                slugs = [s for s in _FALLBACK_SLUGS if s in manifest][:3]

            results: list[ResultItem] = []
            seen: set[str] = set()

            for slug in slugs:
                index = await self._load_index(client, slug)
                if index is None:
                    continue
                for entry in index.get("entries", []):
                    name: str = entry.get("name", "")
                    etype: str = entry.get("type", "")
                    if q in name.lower() or q in etype.lower():
                        key = f"{slug}/{entry.get('path', '')}"
                        if key in seen:
                            continue
                        seen.add(key)
                        results.append(
                            ResultItem(
                                title=name,
                                url=f"{self._base_url}/{slug}/{entry.get('path', '')}",
                                snippet=etype,
                            )
                        )
                        if len(results) >= num_results:
                            break
                if len(results) >= num_results:
                    break

            return SearchResult(results=results[:num_results])

    async def _load_manifest(self, client: httpx.AsyncClient) -> dict | None:
        try:
            resp = await client.get(f"{self._base_url}/docs.json")
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        return None

    async def _load_index(
        self, client: httpx.AsyncClient, slug: str
    ) -> dict | None:
        try:
            resp = await client.get(f"{self._base_url}/docs/{slug}/index.json")
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        return None

    @staticmethod
    def _find_relevant_slugs(query: str, manifest: dict) -> list[str]:
        matched: list[tuple[str, int]] = []
        for slug, info in manifest.items():
            if not isinstance(info, dict):
                continue
            name: str = (info.get("name") or slug).lower()
            aliases: list[str] = [
                a.lower() for a in (info.get("aliases") or [])
            ]
            if query in name or query in slug.lower():
                score = 2 if query == name or query == slug.lower() else 1
                matched.append((slug, score))
            else:
                for a in aliases:
                    if query in a:
                        matched.append((slug, 1))
                        break
        matched.sort(key=lambda x: (-x[1], x[0]))
        return [s for s, _ in matched]

    async def extract(
        self, url: str, options: ExtractOptions
    ) -> ExtractResult:
        raise ProviderError(self.name, "does not support extraction")

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self._base_url}/ping")
                return resp.status_code == 200
        except Exception:
            return False
