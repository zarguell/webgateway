"""Tier 1 deterministic policy engine.

Evaluates YAML-configured :class:`~webgateway.config.PolicyRule` objects to
route web search and extraction requests to the correct provider with the
correct proxy, retry strategy, and DLP policy. The engine is purely
synchronous — it performs pattern matching only, no I/O.

Config is never cached: every :meth:`PolicyEngine.evaluate` call reads from
``ConfigManager.config`` so hot-reloaded configuration takes effect immediately.

Matching precedence within a single ``evaluate`` call:

1. ``policy_override`` — an inline rule dict; only that rule is evaluated.
2. ``provider_override`` — a direct provider name; everything else is default.
3. Ordered ``config.policies`` iteration — first match wins.
4. ``config.defaults`` — when no rule matches.

Error-triggered rules (those specifying ``on_error_class``) are excluded from
normal evaluation and are only consulted via :meth:`evaluate_for_error`.
"""

from __future__ import annotations

import fnmatch
import re
from urllib.parse import urlparse

from webgateway.config import ConfigManager, DefaultsConfig, PolicyMatch, PolicyRule
from webgateway.policy.models import RoutingDecision


class PolicyEngine:
    """Deterministic Tier 1 routing engine.

    Construct once with a :class:`ConfigManager`; the engine reads config
    fresh on every call, so it automatically reflects hot-reloads.
    """

    def __init__(self, config_manager: ConfigManager) -> None:
        self._config_manager = config_manager

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(
        self,
        *,
        content_type: str,
        url: str | None = None,
        query: str | None = None,
        api_key_id: str | None = None,
        provider_override: str | None = None,
        policy_override: dict | None = None,
    ) -> RoutingDecision:
        """Resolve a routing decision for an inbound request.

        Args:
            content_type: ``"search"`` or ``"extract"``.
            url: Target URL (relevant for extract requests and URL/domain rules).
            query: Search query text (relevant for ``query_contains`` rules).
            api_key_id: Identifier of the caller's auth key.
            provider_override: Provider name to force, bypassing rule matching.
            policy_override: Inline rule dict to evaluate in isolation.

        Returns:
            A :class:`RoutingDecision` ready for the router/dispatch layer.
        """
        defaults = self._config_manager.config.defaults

        # a) Inline rule override: evaluate only that single rule.
        if policy_override is not None:
            rule = PolicyRule.model_validate(policy_override)
            if self._rule_matches(
                rule.match,
                content_type=content_type,
                url=url,
                query=query,
                api_key_id=api_key_id,
            ):
                return self._decision_from_rule(rule, content_type, defaults)
            # Override rule did not match the request — fall back to defaults
            # rather than the configured policy list.
            return self._default_decision(content_type, defaults)

        # b) Direct provider override: use it with otherwise-default settings.
        if provider_override is not None:
            decision = self._default_decision(content_type, defaults)
            decision.provider = provider_override
            return decision

        # c) Ordered rule iteration. Error-triggered rules are skipped here so
        # they only fire via evaluate_for_error.
        for rule in self._config_manager.config.policies:
            if rule.match.on_error_class is not None:
                continue
            if self._rule_matches(
                rule.match,
                content_type=content_type,
                url=url,
                query=query,
                api_key_id=api_key_id,
            ):
                return self._decision_from_rule(rule, content_type, defaults)

        # d) No rule matched — use configured defaults.
        return self._default_decision(content_type, defaults)

    def evaluate_for_error(
        self,
        error_class: str | int,
        *,
        content_type: str,
        url: str | None = None,
        query: str | None = None,
        api_key_id: str | None = None,
    ) -> RoutingDecision | None:
        """Find a retry route after a provider error.

        Considers only rules whose ``on_error_class`` includes ``error_class``
        and whose other match criteria also match the request. Returns the first
        match, or ``None`` when no error-triggered rule applies.

        Args:
            error_class: The error class to match — an HTTP status code (int)
                or a symbolic name (e.g. ``"bot_detected"``). Comparison is
                string-based so ``403`` and ``"403"`` match the same entry.
            content_type: ``"search"`` or ``"extract"``.
            url: Target URL of the failing request.
            query: Search query of the failing request.
            api_key_id: Caller key id of the failing request.

        Returns:
            A :class:`RoutingDecision` for the retry, or ``None``.
        """
        defaults = self._config_manager.config.defaults
        error_token = str(error_class)

        for rule in self._config_manager.config.policies:
            if rule.match.on_error_class is None:
                continue
            if not any(str(item) == error_token for item in rule.match.on_error_class):
                continue
            if self._rule_matches(
                rule.match,
                content_type=content_type,
                url=url,
                query=query,
                api_key_id=api_key_id,
            ):
                return self._decision_from_rule(rule, content_type, defaults)

        return None

    # ------------------------------------------------------------------
    # Decision builders
    # ------------------------------------------------------------------

    def _decision_from_rule(
        self,
        rule: PolicyRule,
        content_type: str,
        defaults: DefaultsConfig,
    ) -> RoutingDecision:
        """Build a :class:`RoutingDecision` from a matched rule.

        Per-rule fields override defaults; anything the rule leaves unset falls
        back to ``config.defaults``. ``max_attempts`` always comes from the
        default retry config (the rule model has no per-rule attempt cap).
        """
        if content_type == "search":
            provider = (
                rule.search_provider
                if rule.search_provider is not None
                else defaults.search_provider
            )
        else:
            provider = (
                rule.extract_provider
                if rule.extract_provider is not None
                else defaults.extract_provider
            )

        fallback_chain = (
            list(rule.fallback_chain)
            if rule.fallback_chain is not None
            else list(defaults.retry.fallback_chain)
        )

        retry_strategy = (
            rule.retry_strategy
            if rule.retry_strategy is not None
            else defaults.retry.strategy
        )

        return RoutingDecision(
            policy_matched=rule.name,
            content_type=content_type,
            provider=provider,
            proxy=rule.proxy,
            fallback_chain=fallback_chain,
            retry_strategy=retry_strategy,
            max_attempts=defaults.retry.max_attempts,
            dlp_policy=rule.dlp_policy,
            allowed_providers=rule.allowed_providers,
            playwright_profile=rule.playwright_profile,
        )

    def _default_decision(
        self,
        content_type: str,
        defaults: DefaultsConfig,
    ) -> RoutingDecision:
        """Build a decision purely from ``config.defaults`` (no rule matched)."""
        if content_type == "search":
            provider = defaults.search_provider
        else:
            provider = defaults.extract_provider

        return RoutingDecision(
            policy_matched=None,
            content_type=content_type,
            provider=provider,
            proxy=None,
            fallback_chain=list(defaults.retry.fallback_chain),
            retry_strategy=defaults.retry.strategy,
            max_attempts=defaults.retry.max_attempts,
            dlp_policy=None,
            allowed_providers=None,
            playwright_profile=None,
        )

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------

    def _rule_matches(
        self,
        match: PolicyMatch,
        *,
        content_type: str,
        url: str | None,
        query: str | None,
        api_key_id: str | None,
    ) -> bool:
        """Return ``True`` if *all* specified match criteria pass.

        Unspecified (``None``) criteria are ignored. ``on_error_class`` is
        deliberately not evaluated here — it is handled exclusively by
        :meth:`evaluate_for_error`.
        """
        if match.content_type is not None and match.content_type != content_type:
            return False

        if match.api_key_id is not None and match.api_key_id != api_key_id:
            return False

        if match.domain is not None:
            host = self._extract_host(url)
            domain = match.domain.lower()
            # Auto-detect glob patterns: *.reddit.com, *.example.org, etc.
            if any(c in domain for c in "*?["):
                if not fnmatch.fnmatch(host, domain):
                    return False
            elif domain != host:
                return False

        if match.domain_glob is not None:
            host = self._extract_host(url)
            patterns = (
                match.domain_glob
                if isinstance(match.domain_glob, list)
                else [match.domain_glob]
            )
            if not any(fnmatch.fnmatch(host, pattern.lower()) for pattern in patterns):
                return False

        if match.url_pattern is not None and (
            url is None or re.search(match.url_pattern, url) is None
        ):
            return False

        if match.query_contains is not None:
            if query is None:
                return False
            lowered = query.lower()
            if not any(needle.lower() in lowered for needle in match.query_contains):
                return False

        return True

    @staticmethod
    def _extract_host(url: str | None) -> str:
        """Return the lowercased hostname (no port, no userinfo) for matching.

        ``urlparse(...).hostname`` normalises the host: it strips an explicit
        port, drops any ``user:pass@`` prefix, and lowercases the result — all
        desirable for domain matching. Returns ``""`` when *url* is absent or
        has no host component.
        """
        if not url:
            return ""
        return urlparse(url).hostname or ""
