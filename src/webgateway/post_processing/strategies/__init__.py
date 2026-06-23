"""Extraction strategy registry and selector.

Strategies are tried in priority order (configured per-domain in policy rules).
The first strategy to return non-empty content wins. If no strategy produces
content, the default trafilatura pipeline is used.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from webgateway.config import ConfigManager

logger = logging.getLogger(__name__)


@dataclass
class StrategyResult:
    """Result from a single extraction strategy."""

    content: str
    format: str = "markdown"
    structured_data: dict | list | None = None


class ExtractionStrategy(Protocol):
    """Interface for individual extraction strategies."""

    async def extract(self, html: str, url: str) -> StrategyResult | None:
        """Return extracted content or None if strategy cannot handle the page."""
        ...


class StrategySelector:
    """Selects and runs extraction strategies based on policy config."""

    def __init__(self, config_manager: ConfigManager) -> None:
        self._config = config_manager
        self._strategies: dict[str, ExtractionStrategy] = {}

    def register(self, name: str, strategy: ExtractionStrategy) -> None:
        """Register a named strategy."""
        if name in self._strategies:
            logger.warning("Strategy %r already registered, replacing", name)
        self._strategies[name] = strategy

    async def run(
        self,
        html: str,
        url: str,
        policy_matched: str | None,
    ) -> StrategyResult | None:
        """Run strategies in priority order for the matched policy rule.

        Returns the first non-empty result, or ``None`` if no strategy matched
        (caller should fall back to default trafilatura pipeline).
        """
        if not policy_matched:
            return None

        rule = None
        for r in self._config.config.policies:
            if r.name == policy_matched:
                rule = r
                break

        if rule is None or rule.extract_strategy is None:
            return None

        for strategy_name in rule.extract_strategy.priority:
            if strategy_name == "article_extract":
                continue  # article_extract is the default fallback handled
                          # by the trafilatura pipeline after all strategies
            strategy = self._strategies.get(strategy_name)
            if strategy is None:
                logger.debug("Strategy %r not registered, skipping", strategy_name)
                continue
            try:
                result = await strategy.extract(html, url)
                if result is not None and result.content.strip():
                    logger.debug(
                        "Strategy %r produced content for %s", strategy_name, url
                    )
                    return result
            except Exception:
                logger.exception("Strategy %r failed for %s", strategy_name, url)
                continue

        return None
