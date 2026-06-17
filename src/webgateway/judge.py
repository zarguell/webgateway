"""LLM Judge — Tier 2 routing via a local OpenAI-compatible LLM.

When the deterministic Tier 1 policy engine (YAML rules) produces no match,
or when a provider fails during dispatch, this module calls a local LLM to
make a structured routing decision. The judge always fails open: any LLM
error, parse failure, or low-confidence result returns ``None``, and the
caller falls through to default behavior.

Communication uses any OpenAI-compatible Chat Completions API (LM Studio,
Ollama with ``/v1``, vLLM, etc.).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import dataclass

import httpx

from webgateway.config import ConfigManager
from webgateway.policy.models import RoutingDecision
from webgateway.providers.registry import ProviderRegistry

logger = logging.getLogger(__name__)

__all__ = [
    "DecisionCache",
    "FailedProvider",
    "JudgeContext",
    "JudgeResponse",
    "LLMJudge",
    "ProviderInfo",
    "extract_judge_json",
]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class FailedProvider:
    """A provider that failed during dispatch, with its error context."""

    name: str
    error_class: str
    message: str


@dataclass
class ProviderInfo:
    """Provider metadata surfaced to the judge prompt."""

    name: str
    specialization: str
    healthy: bool
    self_hosted: bool


@dataclass
class JudgeContext:
    """Input bundle for one judge invocation."""

    trigger_type: str  # "on_policy_miss" | "on_retry" | "on_error_class"
    content_type: str  # "search" | "extract"
    url: str | None
    query: str | None
    failed_providers: list[FailedProvider]
    available_providers: list[ProviderInfo]


@dataclass
class JudgeResponse:
    """Parsed JSON output from the LLM."""

    provider: str
    fallback_if_fail: str | None
    reasoning_tag: str
    confidence: float


# ---------------------------------------------------------------------------
# Decision cache
# ---------------------------------------------------------------------------


class DecisionCache:
    """In-memory TTL cache for judge routing decisions.

    Thread-safe via a single ``threading.Lock``. Entries are lazily
    evicted on ``get()`` when their TTL has expired.
    """

    def __init__(self, ttl_seconds: int = 3600) -> None:
        self._ttl = ttl_seconds
        self._store: dict[str, tuple[RoutingDecision, float]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> RoutingDecision | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            decision, ts = entry
            if time.time() - ts > self._ttl:
                del self._store[key]
                return None
            return decision

    def set(self, key: str, decision: RoutingDecision) -> None:
        with self._lock:
            self._store[key] = (decision, time.time())

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------

# Matches the first top-level JSON object in a string.
_JSON_OBJECT_RE = re.compile(r"\{[^{}]*\}", re.DOTALL)

_REQUIRED_KEYS = {"provider", "fallback_if_fail", "reasoning_tag", "confidence"}


def extract_judge_json(text: str) -> JudgeResponse | None:
    """Extract and validate a JudgeResponse from raw LLM output text.

    Handles:
    - Clean JSON
    - JSON wrapped in markdown fences (```` ```json ... ``` ````)
    - JSON surrounded by prose

    Returns ``None`` if no valid JSON object with all required keys is found.
    """
    if not text or not text.strip():
        return None

    # Try direct parse first (fast path for clean JSON)
    try:
        data = json.loads(text)
        return _validate_judge_data(data)
    except (json.JSONDecodeError, TypeError):
        pass

    # Fall back to regex extraction — find all JSON object candidates
    for match in _JSON_OBJECT_RE.finditer(text):
        try:
            data = json.loads(match.group())
            result = _validate_judge_data(data)
            if result is not None:
                return result
        except (json.JSONDecodeError, TypeError):
            continue

    return None


def _validate_judge_data(data: object) -> JudgeResponse | None:
    """Validate a parsed dict into a JudgeResponse, or return None."""
    if not isinstance(data, dict):
        return None
    if not _REQUIRED_KEYS.issubset(data.keys()):
        return None
    try:
        provider = str(data["provider"])
        fallback_raw = data["fallback_if_fail"]
        fallback = str(fallback_raw) if fallback_raw is not None else None
        reasoning_tag = str(data["reasoning_tag"])
        confidence = float(data["confidence"])
    except (ValueError, TypeError):
        return None

    if not provider:
        return None
    confidence = max(0.0, min(1.0, confidence))

    return JudgeResponse(
        provider=provider,
        fallback_if_fail=fallback,
        reasoning_tag=reasoning_tag,
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Cache key derivation
# ---------------------------------------------------------------------------


def _cache_key(context: JudgeContext) -> str:
    """Derive a deterministic cache key from judge context."""
    failed_str = ",".join(
        f"{f.name}:{f.error_class}" for f in context.failed_providers
    )
    providers_str = ",".join(p.name for p in context.available_providers)
    raw = (
        f"{context.trigger_type}|{context.content_type}|"
        f"{context.url or ''}|{context.query or ''}|"
        f"{failed_str}|{providers_str}"
    )
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Prompt constants
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_TEMPLATE = """\
You are a routing decision engine for a web gateway. Your job is to select the \
best provider for a web request based on the request context.

Available providers:
{provider_table}

Respond with STRICT JSON only. No prose, no markdown, no explanation.
Output format:
{{"provider": "<name>", "fallback_if_fail": "<name or null>", \
"reasoning_tag": "<snake_case>", "confidence": <0.0-1.0>}}

Rules:
- "provider" must be one of the available providers listed above.
- "fallback_if_fail" must be a different provider from the list, or null.
- "reasoning_tag" is a short snake_case label for the routing decision.
- "confidence" is your confidence in this routing (0.0 to 1.0).

Routing guidelines:
- JS-heavy sites (SPAs, React/Angular/Vue) -> firecrawl or crawl4ai
- Documentation sites (MDN, readthedocs, pkg.go.dev) -> jina or context7
- Cloudflare/bot-protected sites (403 errors) -> firecrawl or crawl4ai
- API docs, versioned library docs -> context7
- General web content -> jina
- Semantic/similarity search queries -> exa
- General search queries -> searxng or brave
- Rate-limited providers (429) -> switch to a different provider"""


# ---------------------------------------------------------------------------
# LLMJudge
# ---------------------------------------------------------------------------


class LLMJudge:
    """Tier 2 LLM routing judge.

    Calls a local OpenAI-compatible LLM to make provider routing decisions
    when Tier 1 YAML rules miss or when a provider fails during dispatch.

    The judge always fails open: any error returns ``None`` and the caller
    falls through to default behavior.
    """

    def __init__(
        self,
        config_manager: ConfigManager,
        provider_registry: ProviderRegistry,
    ) -> None:
        self._config_manager = config_manager
        self._registry = provider_registry
        self._cache = DecisionCache(
            ttl_seconds=config_manager.config.llm_judge.cache_ttl_seconds
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_enabled(self) -> bool:
        """Return True if the judge is enabled in config."""
        return self._config_manager.config.llm_judge.enabled

    def should_trigger_for_error(self, error_class: str) -> bool:
        """Return True if the judge should fire for this error class."""
        config = self._config_manager.config.llm_judge
        if config.trigger_on_retry:
            return True
        return error_class in config.trigger_on_error_class

    async def evaluate_policy_miss(
        self,
        content_type: str,
        url: str | None,
        query: str | None,
    ) -> RoutingDecision | None:
        """Judge trigger: on_policy_miss.

        Called when Tier 1 policy engine returns no matching rule.
        Returns a routing decision or ``None`` (fail open).
        """
        config = self._config_manager.config.llm_judge
        if not config.enabled or not config.trigger_on_policy_miss:
            return None

        context = JudgeContext(
            trigger_type="on_policy_miss",
            content_type=content_type,
            url=url,
            query=query,
            failed_providers=[],
            available_providers=self._build_provider_info(),
        )
        return await self._evaluate(context)

    async def evaluate_for_retry(
        self,
        content_type: str,
        url: str | None,
        query: str | None,
        failed_providers: list[FailedProvider],
    ) -> RoutingDecision | None:
        """Judge triggers: on_retry / on_error_class.

        Called when a provider fails during dispatch.
        Returns a routing decision with a different provider, or ``None``.
        """
        config = self._config_manager.config.llm_judge
        if not config.enabled:
            return None

        if failed_providers:
            last_error = failed_providers[-1].error_class
            if not self.should_trigger_for_error(last_error):
                return None

        context = JudgeContext(
            trigger_type="on_retry",
            content_type=content_type,
            url=url,
            query=query,
            failed_providers=failed_providers,
            available_providers=self._build_provider_info(),
        )
        return await self._evaluate(context)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _build_provider_info(self) -> list[ProviderInfo]:
        """Build provider info list from the registry and config."""
        config = self._config_manager.config
        names = self._registry.list_names()
        infos: list[ProviderInfo] = []
        for name in names:
            provider_cfg = config.providers.get(name)
            specialization = "general"
            self_hosted = False
            if provider_cfg is not None:
                specialization = provider_cfg.specialization or "general"
            infos.append(
                ProviderInfo(
                    name=name,
                    specialization=specialization,
                    healthy=True,
                    self_hosted=self_hosted,
                )
            )
        return infos

    async def _evaluate(self, context: JudgeContext) -> RoutingDecision | None:
        """Shared evaluation flow: cache -> LLM -> parse -> confidence -> decision."""
        config = self._config_manager.config.llm_judge

        # 1. Cache lookup
        if config.cache_decisions:
            key = _cache_key(context)
            cached = self._cache.get(key)
            if cached is not None:
                logger.debug("Judge cache hit for %s", context.trigger_type)
                return cached
        else:
            key = ""

        # 2. Build prompt and call LLM
        messages = self._build_messages(context)
        raw_text = await self._call_llm(messages)
        if raw_text is None:
            return None

        # 3. Parse JSON
        response = extract_judge_json(raw_text)
        if response is None:
            logger.warning("Judge returned unparseable response")
            return None

        # 4. Validate provider exists and hasn't already failed
        available_names = {p.name for p in context.available_providers}
        failed_names = {fp.name for fp in context.failed_providers}
        if response.provider not in available_names:
            logger.warning("Judge suggested unknown provider: %s", response.provider)
            return None
        if response.provider in failed_names:
            logger.warning(
                "Judge suggested already-failed provider: %s", response.provider
            )
            return None

        # 5. Check confidence threshold
        if response.confidence < config.confidence_threshold:
            logger.debug(
                "Judge confidence %.2f below threshold %.2f",
                response.confidence,
                config.confidence_threshold,
            )
            return None

        # 6. Build routing decision
        decision = self._to_routing_decision(response, context.content_type)

        # 7. Cache
        if config.cache_decisions and key:
            self._cache.set(key, decision)

        return decision

    def _build_messages(self, context: JudgeContext) -> list[dict]:
        """Build chat messages for the LLM API call."""
        provider_table = "\n".join(
            f"- {p.name}: {p.specialization}"
            f"{' (self-hosted)' if p.self_hosted else ''}"
            for p in context.available_providers
        )
        system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(provider_table=provider_table)

        lines = [
            f"Trigger: {context.trigger_type}",
            f"Content type: {context.content_type}",
            f"URL: {context.url or 'N/A'}",
            f"Query: {context.query or 'N/A'}",
        ]

        if context.failed_providers:
            lines.append("")
            lines.append("Failed providers:")
            for i, fp in enumerate(context.failed_providers, 1):
                lines.append(f"{i}. {fp.name}: {fp.error_class} - {fp.message}")
            lines.append("")
            lines.append(
                "Select a provider that has not already failed, "
                "and is likely to succeed given the error context."
            )
        else:
            lines.append("")
            lines.append("No policy rule matched this request. Select the best provider.")

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "\n".join(lines)},
        ]

    async def _call_llm(self, messages: list[dict]) -> str | None:
        """Call the OpenAI-compatible Chat Completions API.

        Returns the assistant message content, or ``None`` on any failure.
        """
        config = self._config_manager.config.llm_judge
        try:
            async with httpx.AsyncClient(timeout=config.timeout) as client:
                resp = await client.post(
                    f"{config.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {config.api_key}"},
                    json={
                        "model": config.model,
                        "messages": messages,
                        "temperature": config.temperature,
                        "max_tokens": 2000,  # reasoning models use tokens for thinking
                    },
                )
        except httpx.HTTPError as exc:
            logger.debug("Judge LLM call failed: %s", exc)
            return None

        if resp.status_code >= 400:
            logger.debug("Judge LLM returned HTTP %d", resp.status_code)
            return None

        try:
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as exc:
            logger.debug("Judge LLM response parse error: %s", exc)
            return None

    def _to_routing_decision(
        self, response: JudgeResponse, content_type: str
    ) -> RoutingDecision:
        """Convert a JudgeResponse into a RoutingDecision."""
        if response.fallback_if_fail:
            fallback_chain = [response.provider, response.fallback_if_fail]
        else:
            fallback_chain = [response.provider]

        return RoutingDecision(
            policy_matched="llm_judge",
            content_type=content_type,
            provider=response.provider,
            fallback_chain=fallback_chain,
            judge_invoked=True,
            judge_reasoning_tag=response.reasoning_tag,
        )
