"""Gateway service — the orchestration layer tying all subsystems together.

The :class:`GatewayService` owns the full request lifecycle:

1. Evaluate policy (deterministic routing decision).
2. Resolve the named proxy to a URL.
3. Dispatch to the primary provider (with fallback chain on error).
4. Normalise the provider result into the API response schema.
5. Write a structured audit entry.

Route handlers are intentionally thin — they authenticate, delegate to this
service, and format the HTTP response.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from serp_llm.audit import AuditEntry, AuditLogger
from serp_llm.cache.keys import extract_key, search_key
from serp_llm.cache.quality import validate_content
from serp_llm.cache.store import CacheStore
from serp_llm.cache.ttl import resolve_ttl
from serp_llm.config import ConfigManager
from serp_llm.dlp import DlpBlockedError, DlpMiddleware
from serp_llm.injection.detector import InjectionDetector
from serp_llm.injection.events import EventLogger
from serp_llm.injection.exemptions import is_exempt
from serp_llm.injection.types import InjectionBlockedError
from serp_llm.judge import FailedProvider, LLMJudge
from serp_llm.policy.engine import PolicyEngine
from serp_llm.policy.models import RoutingDecision
from serp_llm.post_processing.pipeline import PostProcessingPipeline
from serp_llm.providers.base import (
    ExtractOptions,
    ProviderError,
    SearchOptions,
)
from serp_llm.providers.registry import ProviderRegistry
from serp_llm.proxy import ProxyResolver
from serp_llm.resource_manager import ProviderResourceManager
from serp_llm.schemas import (
    DryRunResponse,
    ExtractRequest,
    ExtractResponse,
    PolicyDecision,
    PostProcessingInfo,
    PromptInjectionInfo,
    SearchRequest,
    SearchResponse,
    SearchResultItem,
)
from serp_llm.sessions.manager import SessionError, SessionManager
from serp_llm.sessions.models import CookieEntry, SessionData

# Patterns that indicate a provider response was blocked by bot detection.
# When the fallback loop detects these in a response, it auto-injects a
# bot-solving provider (flaresolverr, invisible_playwright) into the
# candidate list — no per-domain policy rule needed.
BOT_BLOCK_PATTERNS: list[str] = [
    # Cloudflare / DDoS-GUARD
    "Please enable JavaScript",
    "Just a moment...",
    "Checking your browser",
    "DDoS protection",
    "Cloudflare",
    "_cf_chl_opt",
    "cf-browser-verification",
    # Generic bot / CAPTCHA detection
    "verify you're human",
    "verify you are human",
    "unusual traffic from your",
    "security check",
    # hCaptcha / reCAPTCHA
    "h-captcha",
    "hcaptcha",
    "recaptcha/api",
    # Rate limiting
    "too many requests",
    "please try again later",
    # Access control
    "Access Denied",
    "access denied",
    # Geetest (Chinese CAPTCHA)
    "geetest",
    "captcha-delivery.com",
    # DataDome
    "var dd=",
]

_BOT_SOLVERS: list[str] = ["flaresolverr", "invisible_playwright"]


def _is_bot_block(content: str) -> bool:
    """Return True if *content* matches bot-block patterns."""
    return any(p in content for p in BOT_BLOCK_PATTERNS)

__all__ = ["GatewayService"]

logger = logging.getLogger(__name__)


class GatewayService:
    """Orchestrates the full request flow from policy to provider to audit.

    Constructed once at application startup (see :mod:`serp_llm.main`) and
    stored on ``app.state.gateway_service``. Every request that flows through
    the REST or MCP surface calls into this service.
    """

    def __init__(
        self,
        config_manager: ConfigManager,
        policy_engine: PolicyEngine,
        provider_registry: ProviderRegistry,
        proxy_resolver: ProxyResolver,
        audit_logger: AuditLogger,
        cache_store: CacheStore | None = None,
        dlp_middleware: DlpMiddleware | None = None,
        resource_manager: ProviderResourceManager | None = None,
        session_manager: SessionManager | None = None,
        post_processing: PostProcessingPipeline | None = None,
        llm_judge: LLMJudge | None = None,
        injection_detector: InjectionDetector | None = None,
        event_logger: EventLogger | None = None,
    ) -> None:
        self._config_manager = config_manager
        self._policy_engine = policy_engine
        self._provider_registry = provider_registry
        self._proxy_resolver = proxy_resolver
        self._audit_logger = audit_logger
        self._cache_store = cache_store
        self._dlp = dlp_middleware
        self._resource_manager = resource_manager
        self._session_manager = session_manager
        self._post_processing = post_processing
        self._judge = llm_judge
        self._injection_detector = injection_detector
        self._event_logger = event_logger

        max_concurrency = self._config_manager.config.max_concurrency
        self._semaphore = asyncio.Semaphore(max_concurrency)
        logger.info(
            "Concurrency limited to %d in-flight request(s)",
            max_concurrency,
        )

    async def _maybe_judge_policy_miss(
        self,
        decision: RoutingDecision,
        *,
        content_type: str,
        url: str | None,
        query: str | None,
    ) -> RoutingDecision:
        """Tier 2: LLM Judge on policy miss.

        If Tier 1 produced no match and the judge is enabled, ask the judge
        for a routing decision. Returns the judge decision on success, or
        the original decision if the judge declines or fails.
        """
        if decision.policy_matched is not None:
            return decision
        if self._judge is None or not self._judge.is_enabled():
            return decision

        judged = await self._judge.evaluate_policy_miss(
            content_type=content_type,
            url=url,
            query=query,
        )
        if judged is not None:
            return judged
        return decision

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(
        self,
        request: SearchRequest,
        api_key_id: str,
        *,
        dry_run: bool = False,
    ) -> SearchResponse | DryRunResponse:
        """Execute a search request through the full pipeline.

        Args:
            request: Validated search request body.
            api_key_id: Identifier of the caller's auth key (for audit).
            dry_run: If ``True``, return the policy decision without executing.

        Returns:
            A :class:`SearchResponse` on success, or :class:`DryRunResponse`
            when *dry_run* is ``True``.

        Raises:
            ProviderError: If the primary provider and all fallbacks fail.
        """
        request_id = self._generate_request_id()

        decision = self._policy_engine.evaluate(
            content_type="search",
            query=request.query,
            api_key_id=api_key_id,
            provider_override=request.provider,
            policy_override=request.policy_override,
        )

        # Tier 2: LLM Judge on policy miss
        decision = await self._maybe_judge_policy_miss(
            decision,
            content_type="search",
            url=None,
            query=request.query,
        )

        if dry_run:
            return DryRunResponse(
                decision=self._to_policy_decision(decision),
                request_id=request_id,
            )

        start = time.perf_counter()

        dlp_outcome = None
        if self._dlp is not None:
            dlp_outcome = self._dlp.check_outbound(
                request.query, decision.provider
            )
            if dlp_outcome.action == "block":
                latency_ms = max(1, int((time.perf_counter() - start) * 1000))
                await self._audit_logger.log(
                    AuditEntry(
                        request_id=request_id,
                        api_key_id=api_key_id,
                        type="search",
                        url="",
                        provider_used=decision.provider,
                        latency_ms=latency_ms,
                        status="blocked",
                        policy_matched=decision.policy_matched,
                        dlp_policy=dlp_outcome.policy_name,
                        dlp_action="block",
                        dlp_match_count=len(dlp_outcome.matches),
                    )
                )
                raise DlpBlockedError(
                    dlp_outcome.policy_name, dlp_outcome.matches
                )
            if dlp_outcome.action == "reroute":
                decision.provider = dlp_outcome.reroute_to  # type: ignore[assignment]
            elif (
                dlp_outcome.action == "redact"
                and dlp_outcome.redacted_text is not None
            ):
                request = request.model_copy(
                    update={"query": dlp_outcome.redacted_text}
                )

        cache_enabled = (
            self._cache_store is not None
            and self._config_manager.config.cache.enabled
        )
        cache_read = cache_enabled
        cache_write = cache_enabled
        ttl_override = None
        if request.cache is not None:
            cache_read = cache_enabled and request.cache.read
            cache_write = cache_enabled and request.cache.write
            ttl_override = request.cache.ttl_override

        if cache_read:
            key = search_key(
                request.query, decision.provider, request.num_results
            )
            cached = await self._cache_store.get(key)
            if cached is not None:
                cached_json, age_seconds = cached
                latency_ms = max(1, int((time.perf_counter() - start) * 1000))
                cached_response = SearchResponse.model_validate_json(
                    cached_json
                )
                cached_response.request_id = request_id
                cached_response.latency_ms = latency_ms
                cached_response.cached = True
                cached_response.cache_age_seconds = int(age_seconds)

                await self._audit_logger.log(
                    AuditEntry(
                        request_id=request_id,
                        api_key_id=api_key_id,
                        type="search",
                        url="",
                        provider_used=cached_response.provider_used,
                        latency_ms=latency_ms,
                        status="success",
                        policy_matched=decision.policy_matched,
                        proxy_used=decision.proxy,
                        cache_hit=True,
                    )
                )
                return cached_response

        proxy_url = self._proxy_resolver.resolve(decision.proxy)
        options = SearchOptions(
            num_results=request.num_results,
            proxy_url=proxy_url,
            timeout=self._config_manager.config.defaults.timeout,
        )

        try:
            async with self._semaphore:
                result, provider_used, _ = await self._execute_with_fallback(
                    decision.provider,
                    decision.fallback_chain,
                    lambda provider, opts: provider.search(request.query, opts),
                    options,
                    content_type="search",
                    url=None,
                    query=request.query,
                    decision=decision,
                )
        except ProviderError:
            latency_ms = int((time.perf_counter() - start) * 1000)
            await self._audit_logger.log(
                AuditEntry(
                    request_id=request_id,
                    api_key_id=api_key_id,
                    type="search",
                    url="",
                    provider_used=decision.provider,
                    latency_ms=latency_ms,
                    status="error",
                    policy_matched=decision.policy_matched,
                    proxy_used=decision.proxy,
                )
            )
            raise

        latency_ms = int((time.perf_counter() - start) * 1000)

        if self._resource_manager is not None:
            await self._resource_manager.record_success(provider_used)
            await self._resource_manager.record_usage(
                provider_used, "search", request_id, True, latency_ms,
            )

        dlp_in_count = 0
        if self._dlp is not None:
            for item in result.results:
                dlp_in = self._dlp.check_inbound(
                    item.snippet, provider_used
                )
                if dlp_in.action == "redact" and dlp_in.redacted_text is not None:
                    item.snippet = dlp_in.redacted_text
                    dlp_in_count += len(dlp_in.matches)

        await self._audit_logger.log(
            AuditEntry(
                request_id=request_id,
                api_key_id=api_key_id,
                type="search",
                url="",
                provider_used=provider_used,
                latency_ms=latency_ms,
                status="success",
                policy_matched=decision.policy_matched,
                proxy_used=decision.proxy,
                cache_hit=False,
                dlp_policy=(
                    dlp_outcome.policy_name
                    if dlp_outcome and dlp_outcome.action != "pass"
                    else None
                ),
                dlp_action=(
                    dlp_outcome.action
                    if dlp_outcome and dlp_outcome.action != "pass"
                    else None
                ),
                dlp_match_count=(
                    (len(dlp_outcome.matches) if dlp_outcome else 0)
                    + dlp_in_count
                ),
            )
        )

        response = SearchResponse(
            results=[
                SearchResultItem(
                    title=item.title,
                    url=item.url,
                    snippet=item.snippet,
                    published_date=item.published_date,
                )
                for item in result.results
            ],
            provider_used=provider_used,
            request_id=request_id,
            latency_ms=latency_ms,
            cached=False,
        )

        if cache_write:
            cache_cfg = self._config_manager.config.cache
            ttl = ttl_override if ttl_override is not None else resolve_ttl(
                cache_cfg.rules,
                cache_cfg.default_ttl,
                provider=provider_used,
                content_type="search",
            )
            key = search_key(
                request.query, decision.provider, request.num_results
            )
            await self._cache_store.set(
                key,
                response.model_dump_json(),
                ttl,
                content_type="search",
                provider=provider_used,
                query=request.query,
            )

        return response

    # ------------------------------------------------------------------
    # Extract
    # ------------------------------------------------------------------

    async def extract(
        self,
        request: ExtractRequest,
        api_key_id: str,
        *,
        dry_run: bool = False,
    ) -> ExtractResponse | DryRunResponse:
        """Execute an extraction request through the full pipeline.

        Args:
            request: Validated extraction request body.
            api_key_id: Identifier of the caller's auth key (for audit).
            dry_run: If ``True``, return the policy decision without executing.

        Returns:
            An :class:`ExtractResponse` on success, or :class:`DryRunResponse`
            when *dry_run* is ``True``.

        Raises:
            ProviderError: If the primary provider and all fallbacks fail.
        """
        request_id = self._generate_request_id()

        decision = self._policy_engine.evaluate(
            content_type="extract",
            url=str(request.url),
            api_key_id=api_key_id,
            provider_override=request.provider,
            policy_override=request.policy_override,
        )

        # Tier 2: LLM Judge on policy miss
        decision = await self._maybe_judge_policy_miss(
            decision,
            content_type="extract",
            url=str(request.url),
            query=None,
        )

        if request.format == "text":
            if decision.policy_matched is not None:
                # A policy rule matched — the site has specialized extraction
                # (strategy, readability). Don't use text mode; let the
                # pipeline handle it in HTML mode for best results.
                request.format = "markdown"
            elif request.provider is None:
                # No policy matched and no provider override — auto-route
                # to InvisiblePlaywright since text_mode requires a browser.
                decision.provider = "invisible_playwright"

        if dry_run:
            return DryRunResponse(
                decision=self._to_policy_decision(decision),
                request_id=request_id,
            )

        # --- SSRF check: validate the URL before any provider dispatch ---
        from serp_llm.security.url_validator import validate_url

        try:
            validate_url(str(request.url))
        except ValueError as exc:
            raise ProviderError(
                provider="gateway",
                status_code=400,
                error_class="bad_request",
                message=str(exc),
            ) from exc

        start = time.perf_counter()

        dlp_outcome = None
        if self._dlp is not None:
            dlp_outcome = self._dlp.check_outbound(
                str(request.url), decision.provider
            )
            if dlp_outcome.action == "block":
                latency_ms = max(1, int((time.perf_counter() - start) * 1000))
                await self._audit_logger.log(
                    AuditEntry(
                        request_id=request_id,
                        api_key_id=api_key_id,
                        type="extract",
                        url=str(request.url),
                        provider_used=decision.provider,
                        latency_ms=latency_ms,
                        status="blocked",
                        policy_matched=decision.policy_matched,
                        dlp_policy=dlp_outcome.policy_name,
                        dlp_action="block",
                        dlp_match_count=len(dlp_outcome.matches),
                    )
                )
                raise DlpBlockedError(
                    dlp_outcome.policy_name, dlp_outcome.matches
                )
            if dlp_outcome.action == "reroute":
                decision.provider = dlp_outcome.reroute_to  # type: ignore[assignment]

        cache_enabled = (
            self._cache_store is not None
            and self._config_manager.config.cache.enabled
        )
        cache_read = cache_enabled
        cache_write = cache_enabled
        ttl_override = None
        if request.cache is not None:
            cache_read = cache_enabled and request.cache.read
            cache_write = cache_enabled and request.cache.write
            ttl_override = request.cache.ttl_override

        session_data: SessionData | None = None
        if request.session_profile is not None and self._session_manager is not None:
            domain = urlparse(str(request.url)).hostname or ""

            try:
                session_data = await self._session_manager.resolve(
                    request.session_profile,
                    provider_name=decision.provider,
                    domain=domain,
                    proxy_name=decision.proxy,
                )
            except SessionError as exc:
                latency_ms = max(1, int((time.perf_counter() - start) * 1000))
                await self._audit_logger.log(
                    AuditEntry(
                        request_id=request_id,
                        api_key_id=api_key_id,
                        type="extract",
                        url=str(request.url),
                        provider_used=decision.provider,
                        latency_ms=latency_ms,
                        status="error",
                        policy_matched=decision.policy_matched,
                        proxy_used=decision.proxy,
                        session_profile=request.session_profile,
                        session_valid=False,
                        session_expired=(exc.error_class == "session_expired"),
                    )
                )
                raise

            cache_read = False
            cache_write = False

        if cache_read:
            key = extract_key(
                str(request.url),
                request.format,
                request.session_profile,
                decision.provider,
                pp_skip=bool(request.post_processing and request.post_processing.skip),
            )
            cached = await self._cache_store.get(key)
            if cached is not None:
                cached_json, age_seconds = cached
                latency_ms = max(1, int((time.perf_counter() - start) * 1000))
                cached_response = ExtractResponse.model_validate_json(
                    cached_json
                )
                cached_response.request_id = request_id
                cached_response.latency_ms = latency_ms
                cached_response.cached = True
                cached_response.cache_age_seconds = int(age_seconds)

                await self._audit_logger.log(
                    AuditEntry(
                        request_id=request_id,
                        api_key_id=api_key_id,
                        type="extract",
                        url=str(request.url),
                        provider_used=cached_response.provider_used,
                        latency_ms=latency_ms,
                        status="success",
                        policy_matched=decision.policy_matched,
                        proxy_used=decision.proxy,
                        cache_hit=True,
                    )
                )
                return cached_response

        proxy_url = self._proxy_resolver.resolve(decision.proxy)
        options = ExtractOptions(
            format=request.format,
            proxy_url=proxy_url,
            wait_for_selector=request.wait_for_selector,
            timeout=self._config_manager.config.defaults.timeout,
            session_id=request.session_profile,
            session_cookies=(
                {c.name: c.value for c in session_data.cookies}
                if session_data is not None and session_data.cookies
                else None
            ),
            fingerprint_id=session_data.fingerprint_id if session_data is not None else None,
            user_agent=session_data.user_agent if session_data is not None else None,
        )

        quality_validator = self._make_quality_validator()

        try:
            async with self._semaphore:
                result, provider_used, quality_passed = await self._execute_with_fallback(
                    decision.provider,
                    decision.fallback_chain,
                    lambda provider, opts: provider.extract(str(request.url), opts),
                    options,
                    validator=quality_validator,
                    content_type="extract",
                    url=str(request.url),
                    query=None,
                    decision=decision,
                )
        except ProviderError:
            latency_ms = int((time.perf_counter() - start) * 1000)
            await self._audit_logger.log(
                AuditEntry(
                    request_id=request_id,
                    api_key_id=api_key_id,
                    type="extract",
                    url=str(request.url),
                    provider_used=decision.provider,
                    latency_ms=latency_ms,
                    status="error",
                    policy_matched=decision.policy_matched,
                    proxy_used=decision.proxy,
                    session_profile=request.session_profile,
                )
            )
            raise

        latency_ms = int((time.perf_counter() - start) * 1000)

        if self._resource_manager is not None:
            await self._resource_manager.record_success(provider_used)
            await self._resource_manager.record_usage(
                provider_used, "extract", request_id, True, latency_ms,
            )

        if (
            request.session_profile is not None
            and self._session_manager is not None
            and self._config_manager.config.sessions.auto_invalidate_on_login_wall
        ):
            login_wall_patterns = self._config_manager.config.sessions.login_wall_patterns
            result_content = result.content if hasattr(result, 'content') else ''
            if result_content and any(
                pattern.lower() in result_content.lower()
                for pattern in login_wall_patterns
            ):
                await self._session_manager.invalidate(
                    session_id=request.session_profile
                )
                await self._audit_logger.log(
                    AuditEntry(
                        request_id=request_id,
                        api_key_id=api_key_id,
                        type="extract",
                        url=str(request.url),
                        provider_used=provider_used,
                        latency_ms=latency_ms,
                        status="error",
                        policy_matched=decision.policy_matched,
                        proxy_used=decision.proxy,
                        session_profile=request.session_profile,
                        session_valid=False,
                        session_expired=True,
                    )
                )
                raise SessionError(
                    "session_expired",
                    "Login wall detected. Session invalidated. Refresh cookies.",
                    session_id=request.session_profile,
                )

        # --- post-processing pipeline ---
        pp_info: PostProcessingInfo | None = None
        pi_info: PromptInjectionInfo | None = None
        structured_data: dict | list | None = None
        injection_detected = False
        injection_type: str | None = None
        injection_action: str | None = None
        injection_h_score = 0.0
        injection_c_score = 0.0
        injection_layer: str | None = None
        if (
            self._post_processing is not None
            and request.format != "html"
            and not (request.post_processing and request.post_processing.skip)
        ):
            # --- Determine if injection detection should run ---
            pi_config = self._config_manager.config.prompt_injection
            skip_injection = True
            if pi_config.enabled and self._injection_detector is not None:
                override_skip = bool(
                    request.prompt_injection and request.prompt_injection.skip
                )
                exempt = is_exempt(
                    url=str(request.url),
                    api_key_id=api_key_id,
                    exempt_domains=pi_config.exemptions.domains,
                    exempt_api_key_ids=pi_config.exemptions.api_key_ids,
                )
                skip_injection = override_skip or exempt

            pp_result = await self._post_processing.run(
                content=result.content,
                url=str(request.url),
                format=result.format,
                provider=provider_used,
                policy_matched=decision.policy_matched,
                skip_injection=skip_injection,
            )
            result.content = pp_result.content
            result.format = pp_result.format
            structured_data = pp_result.structured_data
            pp_info = PostProcessingInfo(
                extractor_used=pp_result.extractor_used,
                extraction_fallback=pp_result.extraction_fallback,
                content_length_raw=pp_result.content_length_raw,
                content_length_processed=pp_result.content_length_processed,
                reduction_pct=pp_result.reduction_pct,
                content_unchanged=pp_result.content_unchanged,
                content_hash=pp_result.content_hash,
            )

            # --- Handle injection detection result ---
            if pp_result.injection is not None:
                inj = pp_result.injection
                pi_info = PromptInjectionInfo(
                    checked=inj.checked,
                    detected=inj.detected,
                    injection_type=inj.injection_type,
                    layer_triggered=inj.layer_triggered,
                    classifier_score=inj.classifier_score,
                    heuristic_score=inj.heuristic_score,
                    action_taken=inj.action,
                    scrubbed_segments=inj.scrubbed_segments,
                )
                injection_detected = inj.detected
                injection_type = inj.injection_type
                injection_action = inj.action
                injection_h_score = inj.heuristic_score
                injection_c_score = inj.classifier_score
                injection_layer = inj.layer_triggered

                # Block action → raise
                if inj.action == "block":
                    if self._event_logger:
                        self._event_logger.log_event(
                            event="injection_detected",
                            url=str(request.url),
                            request_id=request_id,
                            api_key_id=api_key_id,
                            injection_type=inj.injection_type,
                            heuristic_score=inj.heuristic_score,
                            classifier_score=inj.classifier_score,
                            layer_triggered=inj.layer_triggered,
                            action_taken="block",
                        )
                    raise InjectionBlockedError(
                        url=str(request.url),
                        injection_type=inj.injection_type,
                        layer_triggered=inj.layer_triggered,
                        heuristic_score=inj.heuristic_score,
                        classifier_score=inj.classifier_score,
                    )

                # Alert/scrub → write event
                if inj.detected and self._event_logger:
                    self._event_logger.log_event(
                        event="injection_detected",
                        url=str(request.url),
                        request_id=request_id,
                        api_key_id=api_key_id,
                        injection_type=inj.injection_type,
                        heuristic_score=inj.heuristic_score,
                        classifier_score=inj.classifier_score,
                        layer_triggered=inj.layer_triggered,
                        action_taken=inj.action,
                    )

        dlp_in_count = 0
        if self._dlp is not None:
            dlp_in = self._dlp.check_inbound(result.content, provider_used)
            if dlp_in.action == "block":
                await self._audit_logger.log(
                    AuditEntry(
                        request_id=request_id,
                        api_key_id=api_key_id,
                        type="extract",
                        url=str(request.url),
                        provider_used=provider_used,
                        latency_ms=latency_ms,
                        status="blocked",
                        policy_matched=decision.policy_matched,
                        dlp_policy=dlp_in.policy_name,
                        dlp_action="block",
                        dlp_match_count=len(dlp_in.matches),
                    )
                )
                raise DlpBlockedError(dlp_in.policy_name, dlp_in.matches)
            if dlp_in.action == "redact" and dlp_in.redacted_text is not None:
                result.content = dlp_in.redacted_text
                dlp_in_count = len(dlp_in.matches)

        await self._audit_logger.log(
            AuditEntry(
                request_id=request_id,
                api_key_id=api_key_id,
                type="extract",
                url=str(request.url),
                provider_used=provider_used,
                latency_ms=latency_ms,
                status="success",
                policy_matched=decision.policy_matched,
                proxy_used=decision.proxy,
                cache_hit=False,
                quality_check_passed=quality_passed,
                extractor_used=pp_info.extractor_used if pp_info else None,
                extraction_fallback=pp_info.extraction_fallback if pp_info else False,
                content_length_raw=pp_info.content_length_raw if pp_info else 0,
                content_length_processed=pp_info.content_length_processed if pp_info else 0,
                content_unchanged=pp_info.content_unchanged if pp_info else False,
                session_profile=request.session_profile,
                session_valid=True,
                fingerprint_id=(
                    session_data.fingerprint_id if session_data is not None else None
                ),
                browser_service=(
                    session_data.browser_service if session_data is not None else None
                ),
                browser_engine="firefox" if session_data is not None else None,
                dlp_policy=(
                    dlp_outcome.policy_name
                    if dlp_outcome and dlp_outcome.action != "pass"
                    else None
                ),
                dlp_action=(
                    dlp_outcome.action
                    if dlp_outcome and dlp_outcome.action != "pass"
                    else None
                ),
                dlp_match_count=(
                    (len(dlp_outcome.matches) if dlp_outcome else 0)
                    + dlp_in_count
                ),
                injection_checked=pi_info.checked if pi_info else False,
                injection_detected=injection_detected,
                injection_type=injection_type,
                injection_action=injection_action,
                injection_heuristic_score=injection_h_score,
                injection_classifier_score=injection_c_score,
                injection_layer_triggered=injection_layer,
            )
        )

        response = ExtractResponse(
            content=result.content,
            format=result.format,
            url=str(request.url),
            provider_used=provider_used,
            request_id=request_id,
            latency_ms=latency_ms,
            cached=False,
            quality_warning=not quality_passed,
            post_processing=pp_info,
            prompt_injection=pi_info,
            structured_data=structured_data,
        )

        if cache_write and quality_passed:
            cache_cfg = self._config_manager.config.cache
            ttl = ttl_override if ttl_override is not None else resolve_ttl(
                cache_cfg.rules,
                cache_cfg.default_ttl,
                provider=provider_used,
                url=str(request.url),
                content_type="extract",
            )
            key = extract_key(
                str(request.url),
                request.format,
                request.session_profile,
                decision.provider,
                pp_skip=bool(request.post_processing and request.post_processing.skip),
            )
            await self._cache_store.set(
                key,
                response.model_dump_json(),
                ttl,
                content_type="extract",
                provider=provider_used,
                url=str(request.url),
            )

        return response

    # ------------------------------------------------------------------
    # Provider health
    # ------------------------------------------------------------------

    async def check_providers_health(self) -> dict[str, bool]:
        """Check the health of all registered providers."""
        return await self._provider_registry.health_check_all()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_request_id() -> str:
        """Generate a request ID: ``req_`` + 6 random hex characters."""
        return f"req_{secrets.token_hex(3)}"

    @staticmethod
    def _to_policy_decision(decision: RoutingDecision) -> PolicyDecision:
        """Map an internal :class:`RoutingDecision` to the API schema."""
        return PolicyDecision(
            policy_matched=decision.policy_matched,
            provider=decision.provider,
            proxy=decision.proxy,
            fallback_chain=list(decision.fallback_chain),
            retry_strategy=decision.retry_strategy,
            dlp_policy=decision.dlp_policy,
            judge_invoked=decision.judge_invoked,
            judge_reasoning_tag=decision.judge_reasoning_tag,
        )

    def _make_quality_validator(
        self,
    ) -> Callable[[Any], tuple[bool, str | None]] | None:
        triggers = self._config_manager.config.cache.invalidation_triggers
        if not triggers:
            return None
        trigger_dicts = [t.model_dump() for t in triggers]

        def validator(result: Any) -> tuple[bool, str | None]:
            content = getattr(result, "content", "")
            return validate_content(content, trigger_dicts)

        return validator

    async def _execute_with_fallback(
        self,
        provider_name: str,
        fallback_chain: list[str],
        operation: Callable[..., Any],
        *args: Any,
        validator: Callable[[Any], tuple[bool, str | None]] | None = None,
        content_type: str = "",
        url: str | None = None,
        query: str | None = None,
        decision: RoutingDecision | None = None,
    ) -> tuple[Any, str, bool]:
        """Try the primary provider, then each fallback on failure.

        When *validator* is provided, each provider's result is validated
        before being accepted.  A failed validation triggers fallback just
        like a :class:`ProviderError`.  If every provider fails validation,
        the last result is returned with ``quality_passed=False``.

        On provider errors, Tier 1 error policy rules and (optionally) the
        Tier 2 LLM Judge may insert a different provider into the candidate
        list.  *decision* is mutated in place so audit can observe whether
        the judge was consulted.

        When a bot block is detected in the result, the gateway
        automatically:
        1. Checks for cached FlareSolverr cookies for the target host.
        2. If cached, injects cookies and retries the original browser
           provider.
        3. If not cached, dispatches FlareSolverr to solve the challenge,
           stores the resulting cookies, and retries the browser provider.

        Returns:
            ``(result, provider_name, quality_passed)``.
        """
        candidates = [provider_name] + [
            name for name in fallback_chain if name != provider_name
        ]

        # Circuit breaker + quota filtering
        if self._resource_manager is not None:
            candidates = await self._resource_manager.filter_available(candidates)
            if not candidates:
                raise ProviderError(
                    provider_name,
                    "All providers unavailable (circuit open or quota exhausted)",
                    status_code=503,
                    error_class="all_providers_unavailable",
                )

        failed_providers: list[FailedProvider] = []
        last_result: Any = None
        last_provider: str = provider_name

        mutable_args: list[Any] = list(args)
        flaresolverr_cookies_stored = False
        original_browser_provider: str | None = None

        for idx, candidate_name in enumerate(candidates):
            try:
                provider = self._provider_registry.get(candidate_name)
                result = await operation(provider, *mutable_args)

                if validator is not None:
                    passed, _reason = validator(result)
                    if not passed:
                        last_result = result
                        last_provider = candidate_name
                        content = getattr(result, "content", "")

                        if _is_bot_block(content):
                            host = urlparse(url).hostname if url else None

                            if (
                                host
                                and self._session_manager is not None
                                and not flaresolverr_cookies_stored
                            ):
                                fs_cookies = await self._load_flaresolverr_cookies(
                                    host,
                                )
                                if fs_cookies:
                                    mutable_args = self._inject_cookies_into_args(
                                        mutable_args, fs_cookies,
                                    )
                                    flaresolverr_cookies_stored = True
                                    if original_browser_provider is None:
                                        original_browser_provider = candidate_name
                                    self._insert_candidate(
                                        candidates, original_browser_provider, idx + 1,
                                    )
                                    if idx < len(candidates) - 1:
                                        continue
                                    return result, candidate_name, False

                            for solver in _BOT_SOLVERS:
                                if (
                                    solver not in candidates
                                    and self._provider_registry.has(solver)
                                ):
                                    candidates.append(solver)

                            if (
                                original_browser_provider is None
                                and candidate_name != "flaresolverr"
                            ):
                                original_browser_provider = candidate_name

                        if idx < len(candidates) - 1:
                            continue
                        return result, candidate_name, False

                # FlareSolverr cookie extraction and browser retry
                if (
                    candidate_name == "flaresolverr"
                    and hasattr(result, "cookies")
                    and result.cookies
                    and url
                ):
                    await self._save_flaresolverr_cookies(url, result.cookies)
                    flaresolverr_cookies_stored = True

                    if original_browser_provider is not None:
                        fs_cookies = {
                            c["name"]: c["value"]
                            for c in result.cookies
                            if isinstance(c, dict) and "name" in c
                        }
                        if fs_cookies:
                            mutable_args = self._inject_cookies_into_args(
                                mutable_args, fs_cookies,
                            )
                            self._insert_candidate(
                                candidates,
                                original_browser_provider,
                                idx + 1,
                            )

                return result, candidate_name, True
            except ProviderError as e:
                error_class = str(
                    getattr(e, "error_class", None) or getattr(e, "status_code", "")
                )
                failed_providers.append(FailedProvider(
                    name=candidate_name,
                    error_class=error_class,
                    message=str(e),
                ))

                if self._resource_manager is not None:
                    await self._resource_manager.record_failure(
                        candidate_name,
                        error_class=error_class,
                    )

                # Tier 1: check error-based policy rules
                redirect = self._try_error_policy_redirect(
                    error_class, content_type, url, query,
                    candidates, failed_providers, idx,
                )

                # Tier 2: LLM Judge on retry/error
                if redirect is None:
                    redirect = await self._try_judge_retry_redirect(
                        error_class, content_type, url, query,
                        failed_providers, candidates, idx, decision,
                    )

                if redirect is not None:
                    continue

                if idx == len(candidates) - 1:
                    raise
                continue

        return last_result, last_provider, False

    # ------------------------------------------------------------------
    # FlareSolverr cookie session helpers
    # ------------------------------------------------------------------

    async def _load_flaresolverr_cookies(self, host: str) -> dict[str, str] | None:
        assert self._session_manager is not None
        profile = f"flaresolverr/{host}"
        try:
            session = self._session_manager._store.load(profile)
        except Exception:
            return None

        if not session.cookies:
            return None

        now = time.time()
        if session.expiry_ts is not None and now > session.expiry_ts:
            self._session_manager._store.delete(profile)
            return None

        return {c.name: c.value for c in session.cookies}

    async def _save_flaresolverr_cookies(self, url: str, cookies: list[dict]) -> None:
        if self._session_manager is None:
            return

        host = urlparse(url).hostname or "unknown"
        profile = f"flaresolverr/{host}"
        now = time.time()

        cookie_entries = [
            CookieEntry(
                name=c["name"],
                value=c["value"],
                domain=c.get("domain", host),
                path=c.get("path", "/"),
                secure=c.get("secure", True),
                http_only=c.get("httpOnly", True),
            )
            for c in cookies
            if isinstance(c, dict) and "name" in c
        ]

        session = SessionData(
            session_id=profile,
            browser_service="flaresolverr",
            domain=host,
            cookies=cookie_entries,
            user_agent="",
            fingerprint_id="",
            created_ts=now,
            last_used_ts=now,
            expiry_ts=now + 3600,
        )
        self._session_manager._store.save(session)

    @staticmethod
    def _inject_cookies_into_args(
        args: list[Any], cookies: dict[str, str],
    ) -> list[Any]:
        new_args = list(args)
        for i, arg in enumerate(new_args):
            if isinstance(arg, ExtractOptions):
                merged = (
                    {**arg.session_cookies, **cookies}
                    if arg.session_cookies
                    else cookies
                )
                new_args[i] = ExtractOptions(
                    format=arg.format,
                    proxy_url=arg.proxy_url,
                    wait_for_selector=arg.wait_for_selector,
                    session_cookies=merged,
                    session_id=arg.session_id,
                    fingerprint_id=arg.fingerprint_id,
                    user_agent=arg.user_agent,
                    timeout=arg.timeout,
                )
                break
        return new_args

    def _try_error_policy_redirect(
        self,
        error_class: str,
        content_type: str,
        url: str | None,
        query: str | None,
        candidates: list[str],
        failed_providers: list[FailedProvider],
        idx: int,
    ) -> str | None:
        """Check Tier 1 error rules. Returns redirect provider name or None."""
        if not content_type:
            return None
        error_decision = self._policy_engine.evaluate_for_error(
            error_class,
            content_type=content_type,
            url=url,
            query=query,
            api_key_id=None,
        )
        if error_decision is None:
            return None
        redirect_name = error_decision.provider
        failed_names = {f.name for f in failed_providers}
        if redirect_name in failed_names:
            return None
        self._insert_candidate(candidates, redirect_name, idx + 1)
        return redirect_name

    async def _try_judge_retry_redirect(
        self,
        error_class: str,
        content_type: str,
        url: str | None,
        query: str | None,
        failed_providers: list[FailedProvider],
        candidates: list[str],
        idx: int,
        decision: RoutingDecision | None,
    ) -> str | None:
        """Check Tier 2 LLM judge on retry. Returns redirect provider or None."""
        if self._judge is None or not content_type:
            return None
        if not self._judge.should_trigger_for_error(error_class):
            return None

        judged = await self._judge.evaluate_for_retry(
            content_type=content_type,
            url=url,
            query=query,
            failed_providers=failed_providers,
        )
        if judged is None:
            return None

        redirect_name = judged.provider
        failed_names = {f.name for f in failed_providers}
        if redirect_name in failed_names:
            return None

        # Update decision for audit propagation
        if decision is not None:
            decision.judge_invoked = True
            decision.judge_reasoning_tag = judged.judge_reasoning_tag

        self._insert_candidate(candidates, redirect_name, idx + 1)
        return redirect_name

    @staticmethod
    def _insert_candidate(
        candidates: list[str], name: str, pos: int
    ) -> None:
        """Insert name at pos in candidates, avoiding duplicates.

        If name is already in the remaining candidates (after pos),
        move it to pos instead of duplicating.
        """
        remaining = candidates[pos:]
        while name in remaining:
            remaining.remove(name)
        candidates[pos:] = remaining
        candidates.insert(pos, name)
