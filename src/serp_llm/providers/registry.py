"""Provider registry — instantiates and manages all provider adapters.

Reads provider configuration from the :class:`~serp_llm.config.ConfigManager`
and builds the appropriate adapter for each known, enabled provider type.
"""

from __future__ import annotations

import asyncio
import logging
import time

from serp_llm.config import ConfigManager
from serp_llm.providers.base import ProviderAdapter, ProviderMetadata
from serp_llm.providers.brave import BraveSearchAdapter
from serp_llm.providers.cdp_chrome import CdpChromeAdapter
from serp_llm.providers.context7 import Context7Adapter
from serp_llm.providers.crawl4ai import Crawl4AIAdapter
from serp_llm.providers.devdocs import DevDocsAdapter
from serp_llm.providers.duckduckgo import DuckDuckGoAdapter
from serp_llm.providers.exa import ExaAdapter
from serp_llm.providers.firecrawl import FirecrawlAdapter
from serp_llm.providers.flaresolverr import FlareSolverrAdapter
from serp_llm.providers.invisible_playwright import InvisiblePlaywrightAdapter
from serp_llm.providers.jina import JinaReaderAdapter
from serp_llm.providers.perplexity import PerplexityAdapter
from serp_llm.providers.searxng import SearXNGAdapter
from serp_llm.providers.tavily import TavilyAdapter
from serp_llm.providers.zyte import ZyteAdapter

__all__ = ["ProviderRegistry"]

logger = logging.getLogger(__name__)

# Cache health check results for this many seconds to avoid hammering
# rate-limited upstream APIs (e.g. Brave's 1 req/sec) on every /health poll.
_HEALTH_CACHE_TTL: float = 300.0


class ProviderRegistry:
    """Creates and provides access to provider adapters from configuration."""

    def __init__(self, config_manager: ConfigManager) -> None:
        self._config_manager = config_manager
        self._adapters: dict[str, ProviderAdapter] = {}
        self._health_cache: dict[str, tuple[bool, float]] = {}
        self._build_adapters()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_adapters(self) -> None:
        """Instantiate adapters for every known, enabled provider."""
        providers = self._config_manager.config.providers
        for name, provider_config in providers.items():
            if not provider_config.enabled:
                logger.debug("Skipping disabled provider: %s", name)
                continue
            adapter = self._create_adapter(name, provider_config)
            if adapter is not None:
                self._adapters[name] = adapter

    def _create_adapter(
        self, name: str, provider_config: object
    ) -> ProviderAdapter | None:
        """Build a single adapter for *name*, or log and skip if unknown."""
        from serp_llm.config import ProviderConfig

        assert isinstance(provider_config, ProviderConfig)
        cfg = provider_config

        if name == "searxng":
            return SearXNGAdapter(
                base_url=cfg.base_url or "http://searxng:8080",
                timeout=cfg.timeout or 15,
            )
        if name == "jina":
            return JinaReaderAdapter(
                api_key=cfg.api_key,
                base_url=cfg.base_url or "https://r.jina.ai",
                timeout=cfg.timeout or 15,
            )
        if name == "brave":
            return BraveSearchAdapter(
                api_key=cfg.api_key,
                base_url=cfg.base_url or "https://api.search.brave.com",
                timeout=cfg.timeout or 15,
            )
        if name == "tavily":
            return TavilyAdapter(
                api_key=cfg.api_key,
                base_url=cfg.base_url or "https://api.tavily.com",
                timeout=cfg.timeout or 15,
            )
        if name == "firecrawl":
            return FirecrawlAdapter(
                api_key=cfg.api_key,
                base_url=cfg.base_url or "https://api.firecrawl.dev",
                timeout=cfg.timeout or 15,
            )
        if name == "firecrawl_selfhosted":
            return FirecrawlAdapter(
                api_key=cfg.api_key,
                base_url=cfg.base_url or "http://firecrawl:3002",
                timeout=cfg.timeout or 15,
                self_hosted=True,
            )

        if name == "invisible_playwright":
            return InvisiblePlaywrightAdapter(
                base_url=cfg.base_url or "http://invisible-playwright:3001",
                timeout=cfg.timeout or 15,
                warnings=cfg.warnings,
                firefox_version=cfg.firefox_version or "150",
                cost_units_per_call=cfg.cost_units_per_call or 0.8,
            )

        if name == "context7":
            return Context7Adapter(
                api_key=cfg.api_key,
                base_url=cfg.base_url or "https://context7.com",
                timeout=cfg.timeout or 15,
            )
        if name == "perplexity":
            return PerplexityAdapter(
                api_key=cfg.api_key,
                timeout=cfg.timeout or 15,
            )
        if name == "devdocs":
            return DevDocsAdapter(
                base_url=cfg.base_url or "http://devdocs:9292",
                timeout=cfg.timeout or 15,
            )
        if name == "exa":
            return ExaAdapter(
                api_key=cfg.api_key,
                timeout=cfg.timeout or 15,
            )
        if name == "duckduckgo":
            return DuckDuckGoAdapter(
                timeout=cfg.timeout or 15,
            )
        if name == "zyte":
            return ZyteAdapter(
                api_key=cfg.api_key,
                base_url=cfg.base_url or "https://api.zyte.com",
                timeout=cfg.timeout or 120,
            )
        if name == "crawl4ai":
            return Crawl4AIAdapter(
                base_url=cfg.base_url or "http://crawl4ai:11235",
                timeout=cfg.timeout or 30,
                mode="crawl",
                api_token=cfg.api_key,
            )
        if name == "crawl4ai_md":
            return Crawl4AIAdapter(
                base_url=cfg.base_url or "http://crawl4ai:11235",
                timeout=cfg.timeout or 30,
                mode="md",
                api_token=cfg.api_key,
            )
        if name == "flaresolverr":
            return FlareSolverrAdapter(
                config={
                    "base_url": cfg.base_url or "http://flaresolverr:8191",
                    "max_timeout": (cfg.timeout or 60) * 1000,
                }
            )

        if name == "cdp_chrome":
            return CdpChromeAdapter(
                config={
                    "base_url": cfg.base_url or "http://cdp-chrome:9222",
                    "timeout": cfg.timeout or 30,
                }
            )

        logger.warning("Unknown provider type %r — skipping", name)
        return None

    # ------------------------------------------------------------------
    # Access
    # ------------------------------------------------------------------

    def get(self, name: str) -> ProviderAdapter:
        """Return the adapter registered under *name*.

        Raises KeyError if no such provider is registered.
        """
        try:
            return self._adapters[name]
        except KeyError:
            raise KeyError(f"No provider registered as {name!r}") from None

    def has(self, name: str) -> bool:
        """Return True if an adapter for *name* is registered."""
        return name in self._adapters

    def list_names(self) -> list[str]:
        """Return a sorted list of registered provider names."""
        return sorted(self._adapters)

    def list_metadata(self) -> list[ProviderMetadata]:
        """Return metadata for every registered provider."""
        return [adapter.metadata for adapter in self._adapters.values()]

    async def health_check_all(self) -> dict[str, bool]:
        """Run ``health_check`` on all providers concurrently.

        Results are cached for ``_HEALTH_CACHE_TTL`` seconds so repeated
        calls to ``GET /health`` don't exhaust rate-limited providers.

        Providers with ``health_check_enabled=false`` in their config are
        skipped — they are reported as healthy without making an API call.

        Exceptions are treated as unhealthy rather than propagated.
        """
        provider_configs = self._config_manager.config.providers
        names = list(self._adapters)
        if not names:
            return {}

        now = time.monotonic()
        stale: list[str] = []
        status: dict[str, bool] = {}

        for name in names:
            cfg = provider_configs.get(name)
            if cfg is not None and not cfg.health_check_enabled:
                status[name] = True
                continue
            cached = self._health_cache.get(name)
            if cached is not None and (now - cached[1]) < _HEALTH_CACHE_TTL:
                status[name] = cached[0]
            else:
                stale.append(name)

        if stale:
            results = await asyncio.gather(
                *(self._adapters[n].health_check() for n in stale),
                return_exceptions=True,
            )
            for name, result in zip(stale, results, strict=True):
                healthy = result is True
                status[name] = healthy
                self._health_cache[name] = (healthy, time.monotonic())

        return status
