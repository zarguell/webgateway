"""Routing decision models returned by the policy engine.

A :class:`RoutingDecision` is the deterministic output of Tier 1 rule
evaluation. It carries every signal the router needs to dispatch a request:
the resolved provider, the named proxy (URL resolved later by the proxy
injector), the fallback chain, retry strategy, and any DLP / allowed-provider
constraints.

``judge_invoked`` and ``judge_reasoning_tag`` default to ``False`` / ``None``
because Tier 1 never sets them — they are populated by the Tier 2 LLM judge
when it supplements a decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RoutingDecision:
    """The outcome of evaluating request attributes against policy rules.

    Attributes:
        policy_matched: Name of the rule that matched, or ``None`` when the
            decision was produced from defaults (no rule matched) or a direct
            provider/policy override.
        content_type: ``"search"`` or ``"extract"`` — the request category.
        provider: Resolved provider name to dispatch to.
        proxy: Named proxy to route through (a *name* from config, not a URL;
            the proxy injector resolves the name to a URL later).
        fallback_chain: Ordered list of provider names to try if the primary
            fails, per the configured retry strategy.
        retry_strategy: How failures are retried — ``"fallback"``,
            ``"exponential"``, or ``"none"``.
        max_attempts: Maximum dispatch attempts (including the first).
        dlp_policy: Name of the DLP policy to enforce, if any.
        allowed_providers: If set, restricts routing to these providers only
            (e.g. health queries confined to self-hosted providers).
        playwright_profile: Named Playwright cookie-jar profile, if any.
        judge_invoked: Whether the Tier 2 LLM judge contributed to this
            decision. Always ``False`` for pure Tier 1 evaluation.
        judge_reasoning_tag: Opaque tag from the LLM judge (audit-log only).
            Always ``None`` for pure Tier 1 evaluation.
    """

    policy_matched: str | None
    content_type: str
    provider: str
    proxy: str | None = None
    fallback_chain: list[str] = field(default_factory=list)
    retry_strategy: str = "fallback"
    max_attempts: int = 3
    dlp_policy: str | None = None
    allowed_providers: list[str] | None = None
    playwright_profile: str | None = None
    judge_invoked: bool = False
    judge_reasoning_tag: str | None = None
