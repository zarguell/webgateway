"""Pydantic models for API requests, responses, and internal data transfer.

All external-facing names use "extract" (not "scrape") per the naming convention:
- REST endpoint: POST /extract
- MCP tool: web_extract
- Schema names: ExtractRequest, ExtractResponse
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Cache control (request-level override)
# ---------------------------------------------------------------------------


class CacheControl(BaseModel):
    read: bool = True
    write: bool = True
    ttl_override: int | None = None


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class SearchRequest(BaseModel):
    query: str
    num_results: int = 10
    provider: str | None = None
    policy_override: dict | None = None
    cache: CacheControl | None = None


class SearchResultItem(BaseModel):
    title: str
    url: str
    snippet: str = ""
    published_date: str | None = None


class SearchResponse(BaseModel):
    results: list[SearchResultItem]
    provider_used: str
    request_id: str
    latency_ms: int
    cached: bool = False
    cache_age_seconds: int | None = None
    quality_warning: bool = False


# ---------------------------------------------------------------------------
# Extract (formerly "scrape" at the API surface)
# ---------------------------------------------------------------------------


class ExtractRequest(BaseModel):
    url: str
    format: str = "markdown"
    provider: str | None = None
    policy_override: dict | None = None
    wait_for_selector: str | None = None
    session_profile: str | None = None
    cache: CacheControl | None = None
    post_processing: PostProcessingOverride | None = None
    prompt_injection: PromptInjectionOverride | None = None


class ExtractResponse(BaseModel):
    content: str
    format: str = "markdown"
    url: str
    provider_used: str
    request_id: str
    latency_ms: int
    cached: bool = False
    cache_age_seconds: int | None = None
    quality_warning: bool = False
    post_processing: PostProcessingInfo | None = None
    prompt_injection: PromptInjectionInfo | None = None
    structured_data: dict | list | None = None


# ---------------------------------------------------------------------------
# Health & provider metadata
# ---------------------------------------------------------------------------


class ProviderHealthInfo(BaseModel):
    name: str
    healthy: bool = False
    last_check_ts: str | None = None
    circuit_state: str | None = None  # "closed" | "open" | "half_open"
    quota_pct: float | None = None    # 0.0–100.0


class HealthResponse(BaseModel):
    status: str = "ok"
    providers: list[ProviderHealthInfo] = Field(default_factory=list)


class ProviderMetadataInfo(BaseModel):
    name: str
    self_hosted: bool
    data_retention_days: int | None = None
    trains_on_queries: bool | None = None
    gdpr_compliant: bool = False
    hipaa_compliant: bool = False
    data_residency: list[str] = Field(default_factory=list)
    privacy_policy_url: str | None = None
    mcp_native: bool = False
    capabilities: list[str] = Field(default_factory=list)
    enabled: bool = True
    warnings: list[str] = Field(default_factory=list)
    stealth: bool = False
    engine: str | None = None
    firefox_version: str | None = None
    specialization: str | None = None
    cost_units_per_call: float = 1.0


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------


class ReloadResponse(BaseModel):
    reloaded: bool
    config_hash: str


class CacheInvalidateRequest(BaseModel):
    url: str | None = None
    url_pattern: str | None = None
    provider: str | None = None


class CacheInvalidateResponse(BaseModel):
    invalidated: int


class CacheFlushResponse(BaseModel):
    flushed: int


class CacheStatsResponse(BaseModel):
    total_entries: int
    size_bytes: int
    expired_entries: int


# ---------------------------------------------------------------------------
# Dry-run / policy decision preview
# ---------------------------------------------------------------------------


class PolicyDecision(BaseModel):
    """What the policy engine decided for a request — returned in dry-run mode."""

    policy_matched: str | None = None
    provider: str
    proxy: str | None = None
    fallback_chain: list[str] = Field(default_factory=list)
    retry_strategy: str = "fallback"
    dlp_policy: str | None = None
    judge_invoked: bool = False
    judge_reasoning_tag: str | None = None


class DryRunResponse(BaseModel):
    decision: PolicyDecision
    request_id: str


# ---------------------------------------------------------------------------
# DLP (Data Loss Prevention)
# ---------------------------------------------------------------------------


class DlpBlockResponse(BaseModel):
    """Returned when DLP outbound scan blocks a request (HTTP 403)."""

    detail: str = "Request blocked by DLP policy"
    policy: str | None = None
    matches: list[str] = Field(default_factory=list)


class DlpTestRequest(BaseModel):
    """Admin endpoint: test arbitrary text against DLP rules."""

    text: str
    direction: str = "outbound"  # outbound | inbound
    provider: str = ""


class DlpTestMatchInfo(BaseModel):
    rule_name: str
    action: str
    severity: str
    match_count: int
    sample: str


class DlpTestResponse(BaseModel):
    action: str = "pass"
    policy: str | None = None
    redacted_text: str | None = None
    reroute_to: str | None = None
    matches: list[DlpTestMatchInfo] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Usage / Circuit Breaker admin schemas
# ---------------------------------------------------------------------------


class UsageSummaryItem(BaseModel):
    provider: str
    circuit_state: str = "closed"
    calls_today: int = 0
    calls_month: int = 0
    limit_month: int | None = None
    quota_pct: float | None = None
    cost_units_today: float = 0.0


class UsageSummaryResponse(BaseModel):
    providers: list[UsageSummaryItem]


class UsageHistoryItem(BaseModel):
    date: str
    calls: int
    errors: int
    error_rate: float
    latency_p50_ms: int
    latency_p95_ms: int


class QuotaResetRequest(BaseModel):
    provider: str


class QuotaOverrideRequest(BaseModel):
    provider: str
    remaining: int


class CircuitResetRequest(BaseModel):
    provider: str


# ---------------------------------------------------------------------------
# Session / Cookie Bucket admin schemas
# ---------------------------------------------------------------------------


class CookieEntrySchema(BaseModel):
    name: str
    value: str
    domain: str
    path: str = "/"
    expiry: float | None = None
    secure: bool = True
    http_only: bool = True


class SessionCreateRequest(BaseModel):
    session_id: str
    browser: str = "invisible_playwright"
    domain: str
    cookies: list[CookieEntrySchema]
    user_agent: str
    fingerprint_id: str
    expiry: datetime | None = None
    proxy_binding: str | None = None
    strict_proxy: bool = False


class SessionInfoResponse(BaseModel):
    session_id: str
    domain: str
    browser: str
    engine: str = "firefox"
    created_ts: float
    last_used_ts: float
    expiry: float | None = None
    proxy_binding: str | None = None
    strict_proxy: bool = False
    cookie_count: int = 0
    use_count: int = 0


class SessionStatusResponse(BaseModel):
    session_id: str
    valid: bool
    expired: bool = False
    domain_bound: str | None = None
    browser: str | None = None
    fingerprint_id: str | None = None
    last_used_ts: float | None = None
    use_count: int = 0
    proxy_binding: str | None = None


class SessionInvalidateRequest(BaseModel):
    session_id: str | None = None
    domain: str | None = None
    browser: str | None = None


class SessionRefreshRequest(BaseModel):
    cookies: list[CookieEntrySchema]


class SessionErrorResponse(BaseModel):
    error: str
    error_class: str
    session_id: str | None = None
    message: str


# ---------------------------------------------------------------------------
# Post-processing pipeline metadata
# ---------------------------------------------------------------------------


class PostProcessingOverride(BaseModel):
    skip: bool = False


class PostProcessingInfo(BaseModel):
    extractor_used: str | None = None
    extraction_fallback: bool = False
    content_length_raw: int = 0
    content_length_processed: int = 0
    reduction_pct: float = 0.0
    content_unchanged: bool = False
    content_hash: str | None = None


class PromptInjectionOverride(BaseModel):
    """Per-request override for prompt injection detection (PRD §27.8)."""
    skip: bool = False


class PromptInjectionInfo(BaseModel):
    """Prompt injection detection results surfaced in the response (PRD §27.9)."""
    checked: bool = False
    detected: bool = False
    injection_type: str | None = None
    layer_triggered: str | None = None
    classifier_score: float = 0.0
    heuristic_score: float = 0.0
    action_taken: str = "none"
    scrubbed_segments: int = 0


# ---------------------------------------------------------------------------
# API Key management schemas
# ---------------------------------------------------------------------------


class CreateKeyRequest(BaseModel):
    label: str = ""
    role: str = "operator"


class CreateKeyResponse(BaseModel):
    key_id: str
    secret: str  # shown exactly once
    label: str
    role: str


class KeyInfoResponse(BaseModel):
    key_id: str
    label: str
    role: str
    created_ts: float
    last_used_ts: float | None = None
    revoked: bool = False
    revoked_ts: float | None = None
    secret_prefix: str = ""


class ListKeysResponse(BaseModel):
    keys: list[KeyInfoResponse]


class RevokeKeyResponse(BaseModel):
    key_id: str
    revoked: bool
    revoked_ts: float


class LoginRequest(BaseModel):
    api_key: str


class LoginResponse(BaseModel):
    status: str = "ok"
    redirect: str = "/admin/dashboard"


class LogoutResponse(BaseModel):
    status: str = "ok"
    redirect: str = "/admin/login"
