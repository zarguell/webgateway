"""Configuration loader with hot-reload and environment variable resolution.

Reads a YAML config file, resolves ${ENV_VAR} references, and validates
the structure via Pydantic models. Supports runtime reload via POST /admin/reload.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

_ENV_VAR_PATTERN = re.compile(r"\$\{(\w+)(?::-(.*?))?\}")


def _resolve_env_vars(value: Any) -> Any:
    """Recursively replace ${VAR} references in strings with environment values.

    Supports optional defaults via ``${VAR:-default}`` syntax.
    If a variable is unset and no default is given, the literal ``${VAR}``
    is left in place (so the misconfiguration is visible).
    """

    def _replace_single(m: re.Match[str]) -> str:
        var_name = m.group(1)
        default = m.group(2)  # None when no ":-default" suffix
        env_val = os.environ.get(var_name)
        if env_val is not None:
            return env_val
        return default if default is not None else m.group(0)

    if isinstance(value, str):
        return _ENV_VAR_PATTERN.sub(_replace_single, value)
    if isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_vars(v) for v in value]
    return value


# ---------------------------------------------------------------------------
# Config models
# ---------------------------------------------------------------------------


class RetryConfig(BaseModel):
    strategy: Literal["fallback", "exponential", "none"] = "fallback"
    max_attempts: int = 3
    fallback_chain: list[str] = Field(default_factory=list)


class DefaultsConfig(BaseModel):
    search_provider: str = "searxng"
    extract_provider: str = "jina"
    timeout: int = 15
    retry: RetryConfig = Field(default_factory=RetryConfig)


class PolicyMatch(BaseModel):
    domain: str | None = None
    domain_glob: list[str] | str | None = None
    url_pattern: str | None = None
    api_key_id: str | None = None
    content_type: Literal["search", "extract"] | None = None
    query_contains: list[str] | None = None
    on_error_class: list[str | int] | None = None


class ExtractStrategyConfig(BaseModel):
    """Configuration for per-domain extraction strategies."""

    priority: list[str] = Field(default_factory=lambda: ["article_extract"])


class PolicyRule(BaseModel):
    name: str
    match: PolicyMatch = Field(default_factory=PolicyMatch)
    extract_provider: str | None = None
    search_provider: str | None = None
    proxy: str | None = None
    playwright_profile: str | None = None
    fallback_chain: list[str] | None = None
    retry_strategy: str | None = None
    dlp_policy: str | None = None
    allowed_providers: list[str] | None = None
    extract_strategy: ExtractStrategyConfig | None = None


class ProxyConfig(BaseModel):
    type: Literal["http", "socks5"] = "http"
    url: str


class ProviderConfig(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    rate_limit: int | None = None
    timeout: int | None = None
    enabled: bool = True
    health_check_enabled: bool = True
    stealth: bool = False
    engine: str | None = None
    firefox_version: str | None = None
    specialization: str | None = None
    warnings: list[str] = Field(default_factory=list)
    cost_units_per_call: float = 1.0


class LLMJudgeConfig(BaseModel):
    """Configuration for the Tier 2 LLM Judge.

    Uses any OpenAI-compatible API (LM Studio, Ollama with OpenAI compat, etc.)
    to make routing decisions when Tier 1 policy rules miss.
    """

    enabled: bool = False
    model: str = "google/gemma-4-e2b"
    base_url: str = "http://127.0.0.1:1234/v1"
    api_key: str = "lm-studio"
    # Triggers — when the judge should fire
    trigger_on_policy_miss: bool = True
    trigger_on_retry: bool = True
    trigger_on_error_class: list[str] = Field(
        default_factory=lambda: ["403", "429", "bot_detected", "timeout"]
    )
    # Decision cache
    cache_decisions: bool = True
    cache_ttl_seconds: int = 3600
    # Quality gate
    confidence_threshold: float = 0.70
    # LLM call parameters
    timeout: int = 180  # reasoning models need generous timeout
    temperature: float = 0.0


class DLPRule(BaseModel):
    """A single DLP detection rule.

    Attributes:
        name: Human-readable rule name (e.g. "AWS Access Key").
        pattern: Python regex pattern string.
        action: What to do when the pattern matches.
            - ``block``: reject the request (outbound) or response (inbound).
            - ``redact``: replace the matched text with ``replacement``.
            - ``reroute``: force the request to ``reroute_to`` provider (outbound only).
            - ``log``: record the match in audit log but do not modify.
        replacement: Text to substitute for matches (default ``[REDACTED]``).
            Only used when ``action == "redact"``.
        severity: Impact level for audit logging and operator triage.
        validate_luhn: If True, credit-card matches are post-validated with the
            Luhn checksum. Non-passing matches are discarded.
        reroute_to: Target provider name when ``action == "reroute"``.
    """

    name: str = ""
    pattern: str
    action: Literal["block", "redact", "reroute", "log"] = "block"
    replacement: str = "[REDACTED]"
    severity: Literal["low", "medium", "high", "critical"] = "medium"
    validate_luhn: bool = False
    reroute_to: str | None = None


class DLPPolicy(BaseModel):
    """A named DLP policy applied to specific providers.

    A policy contains separate rule sets for outbound (request-side) and
    inbound (response-side) scanning. The policy only applies when the resolved
    provider is in ``applies_to_providers`` (empty list = all providers).
    """

    name: str
    description: str = ""
    enabled: bool = True
    applies_to_providers: list[str] = Field(default_factory=list)
    outbound_rules: list[DLPRule] = Field(default_factory=list)
    inbound_rules: list[DLPRule] = Field(default_factory=list)


class AuthKey(BaseModel):
    id: str
    secret: str
    label: str = ""
    admin: bool = False


class AuthConfig(BaseModel):
    keys: list[AuthKey] = Field(default_factory=list)


class LoggingConfig(BaseModel):
    path: str = "/app/logs/gateway.jsonl"
    max_bytes: int = 10 * 1024 * 1024
    backup_count: int = 5


class SessionsConfig(BaseModel):
    store_path: str = "/app/sessions"
    encryption_key: str | None = None
    auto_invalidate_on_login_wall: bool = True
    strict_proxy_binding: bool = True
    login_wall_patterns: list[str] = Field(
        default_factory=lambda: [
            "Sign in",
            "Log in to continue",
            "Subscribe to read",
            "Create an account",
            "Your session has expired",
            "Please log in",
            "Access restricted",
        ]
    )


class FingerprintRotationConfig(BaseModel):
    same_domain_window_seconds: int = 3600
    pool_size: int = 10


class StealthConfig(BaseModel):
    fingerprint_rotation: FingerprintRotationConfig = Field(
        default_factory=FingerprintRotationConfig
    )


class CacheMatch(BaseModel):
    provider: list[str] | None = None
    domain_glob: list[str] | None = None
    content_type: str | None = None
    url_pattern: str | None = None


class CacheTTLRule(BaseModel):
    match: CacheMatch = Field(default_factory=CacheMatch)
    ttl: int = 300


class CacheInvalidationTrigger(BaseModel):
    condition: dict[str, Any] = Field(default_factory=dict)
    action: str = "invalidate"


class CacheConfig(BaseModel):
    enabled: bool = True
    backend: Literal["sqlite"] = "sqlite"
    db_path: str = "data/cache.db"
    default_ttl: int = 300
    honor_cache_control_headers: bool = False
    honor_etag: bool = False
    policy_ttl_wins_if_shorter: bool = True
    invalidation_triggers: list[CacheInvalidationTrigger] = Field(default_factory=list)
    rules: list[CacheTTLRule] = Field(default_factory=list)


class RateLimitByKey(BaseModel):
    """Per-key rate limit configuration."""
    requests: int = 60
    window_seconds: int = 60


class RateLimitByIP(BaseModel):
    """Per-IP rate limit configuration."""
    requests: int = 30
    window_seconds: int = 60


class RateLimitConfig(BaseModel):
    """Sliding window rate limiting configuration."""
    enabled: bool = False
    by_key: RateLimitByKey = Field(default_factory=RateLimitByKey)
    by_ip: RateLimitByIP = Field(default_factory=RateLimitByIP)
    cleanup_interval_seconds: int = 300


class CircuitBreakerProviderConfig(BaseModel):
    error_threshold: int = 5
    window_seconds: int = 60
    cooldown_seconds: int = 120
    trip_on: list[str] = Field(default_factory=lambda: ["429", "503", "timeout", "bot_detected"])


class CircuitBreakerConfig(BaseModel):
    enabled: bool = True
    providers: dict[str, CircuitBreakerProviderConfig] = Field(default_factory=dict)


class QuotaProviderConfig(BaseModel):
    monthly_limit: int | None = None
    daily_limit: int | None = None
    alert_at_percent: int = 80
    exhausted_action: Literal["remove_from_pool", "fallback_only"] = "remove_from_pool"
    reset_day: int = 1


class QuotasConfig(BaseModel):
    providers: dict[str, QuotaProviderConfig] = Field(default_factory=dict)


class WebhookConfig(BaseModel):
    """Configuration for webhook alert delivery (Slack, Discord, ntfy, generic)."""

    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)


class SmtpConfig(BaseModel):
    """Configuration for SMTP email alert delivery.

    All secret fields (username, password) are intended to be set via
    ``${ENV_VAR}`` interpolation in ``config.yaml``, never hardcoded.
    """

    enabled: bool = False
    host: str | None = None
    port: int = 587
    username: str | None = None
    password: str | None = None
    use_tls: bool = True
    from_addr: str = "serpllm@localhost"
    to_addrs: list[str] = Field(default_factory=list)
    subject_prefix: str = "[serpLLM]"


class AlertConfig(BaseModel):
    """Alert delivery configuration (PRD §18.7).

    Controls which events trigger notifications and through which channels.
    Both webhook and SMTP are optional — configure one or both.
    """

    events: list[str] = Field(default_factory=list)
    suppress_seconds: int = 300
    webhook: WebhookConfig = Field(default_factory=WebhookConfig)
    smtp: SmtpConfig = Field(default_factory=SmtpConfig)


class MCPConfig(BaseModel):
    """Configuration for the downstream MCP server.
    When enabled, a Streamable HTTP MCP endpoint is mounted at
    ``mount_path`` exposing ``web_search`` and ``web_extract`` tools.
    """
    enabled: bool = False
    mount_path: str = "/mcp"
    json_response: bool = True
    stateless: bool = True


class ExtractorConfig(BaseModel):
    """Per-provider pipeline stage overrides."""
    stage1_extractor: Literal["trafilatura", "readability", "none"] = "trafilatura"
    stage2_converter: Literal["markdownify", "html2text", "none"] = "markdownify"
    stage3_clean: bool = True
    stage4_deduplicate: bool = False


class CleaningConfig(BaseModel):
    min_content_length: int = 200
    additional_boilerplate_patterns: list[str] = Field(default_factory=list)


class DedupConfig(BaseModel):
    enabled: bool = False
    store: Literal["sqlite"] = "sqlite"


class PostProcessingConfig(BaseModel):
    default: ExtractorConfig = Field(default_factory=ExtractorConfig)
    providers: dict[str, ExtractorConfig] = Field(default_factory=dict)
    cleaning: CleaningConfig = Field(default_factory=CleaningConfig)
    deduplication: DedupConfig = Field(default_factory=DedupConfig)


class RebuffVectorDBConfig(BaseModel):
    """Vector DB config for Rebuff — disabled in v1 (Full tier upgrade path)."""
    enabled: bool = False
    provider: Literal["chroma_sqlite"] = "chroma_sqlite"
    path: str = "/app/data/injection_vectors"


class RebuffEmbeddingsConfig(BaseModel):
    """Embeddings config for Rebuff vector DB — disabled in v1."""
    provider: Literal["ollama"] = "ollama"
    model: str = "nomic-embed-text"
    url: str = "http://ollama:11434"


class RebuffLayerConfig(BaseModel):
    """Layer 1: Rebuff heuristic pattern matching."""
    enabled: bool = True
    custom_patterns: list[str] = Field(default_factory=list)
    vector_db: RebuffVectorDBConfig = Field(default_factory=RebuffVectorDBConfig)
    embeddings: RebuffEmbeddingsConfig = Field(default_factory=RebuffEmbeddingsConfig)


class OnnxClassifierLayerConfig(BaseModel):
    """Layer 2: MiniLM ONNX binary classifier."""
    enabled: bool = True
    model_path: str = "/app/models/defender-minilm.onnx"
    threshold: float = 0.85


class LlmJudgeLayerConfig(BaseModel):
    """Layer 3: LLM judge escalation (opt-in)."""
    enabled: bool = False
    model: str = "ollama/gemma3:1b"
    excerpt_max_chars: int = 500


class LakeraGuardLayerConfig(BaseModel):
    """Layer 5: Lakera Guard managed API (opt-in, DLP-gated)."""
    enabled: bool = False
    api_key: str = ""
    dlp_acknowledgement: bool = False


class InjectionLayersConfig(BaseModel):
    """All detection layer configurations."""
    rebuff: RebuffLayerConfig = Field(default_factory=RebuffLayerConfig)
    onnx_classifier: OnnxClassifierLayerConfig = Field(default_factory=OnnxClassifierLayerConfig)
    llm_judge: LlmJudgeLayerConfig = Field(default_factory=LlmJudgeLayerConfig)
    lakera_guard: LakeraGuardLayerConfig = Field(default_factory=LakeraGuardLayerConfig)


class InjectionThresholdsConfig(BaseModel):
    """Score thresholds that trigger alert vs block vs judge escalation."""
    heuristic_score_alert: float = 0.5
    heuristic_score_block: float = 0.85
    classifier_score_alert: float = 0.6
    classifier_score_block: float = 0.90
    llm_judge_escalate: float = 0.65


class InjectionActionsConfig(BaseModel):
    """Action to take when each detection condition is met."""
    on_pattern_match: Literal["block", "alert", "scrub"] = "scrub"
    on_high_score: Literal["block", "alert", "scrub"] = "alert"
    on_judge_confirmed: Literal["block", "alert", "scrub"] = "block"
    on_lakera_detected: Literal["block", "alert", "scrub"] = "block"


class InjectionExemptionsConfig(BaseModel):
    """Trusted domains and API keys that skip injection detection."""
    domains: list[str] = Field(default_factory=list)
    api_key_ids: list[str] = Field(default_factory=list)


class PromptInjectionConfig(BaseModel):
    """Top-level prompt injection detection configuration (PRD §27.5)."""
    enabled: bool = False
    layers: InjectionLayersConfig = Field(default_factory=InjectionLayersConfig)
    thresholds: InjectionThresholdsConfig = Field(default_factory=InjectionThresholdsConfig)
    actions: InjectionActionsConfig = Field(default_factory=InjectionActionsConfig)
    exemptions: InjectionExemptionsConfig = Field(default_factory=InjectionExemptionsConfig)


class GatewayConfig(BaseModel):
    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig)
    policies: list[PolicyRule] = Field(default_factory=list)
    proxies: dict[str, ProxyConfig] = Field(default_factory=dict)
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    llm_judge: LLMJudgeConfig = Field(default_factory=LLMJudgeConfig)
    dlp_policies: list[DLPPolicy] = Field(default_factory=list)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    sessions: SessionsConfig = Field(default_factory=SessionsConfig)
    stealth: StealthConfig = Field(default_factory=StealthConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    circuit_breaker: CircuitBreakerConfig = Field(default_factory=CircuitBreakerConfig)
    quotas: QuotasConfig = Field(default_factory=QuotasConfig)
    alerts: AlertConfig = Field(default_factory=AlertConfig)
    rate_limiting: RateLimitConfig = Field(default_factory=RateLimitConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    post_processing: PostProcessingConfig = Field(default_factory=PostProcessingConfig)
    prompt_injection: PromptInjectionConfig = Field(default_factory=PromptInjectionConfig)


# ---------------------------------------------------------------------------
# Config manager
# ---------------------------------------------------------------------------


class ConfigManager:
    """Holds the active configuration. Thread-safe. Supports hot-reload."""

    def __init__(self, config_path: str | Path, *, autoload: bool = True):
        self._path = Path(config_path)
        self._lock = threading.RLock()
        self._config: GatewayConfig | None = None
        self._config_hash: str = ""
        self._loaded_at: datetime | None = None
        if autoload:
            self.reload()

    @property
    def path(self) -> Path:
        return self._path

    def reload(self) -> GatewayConfig:
        """Re-read the config file, resolve env vars, validate, and store."""
        with self._lock:
            raw = yaml.safe_load(self._path.read_text())
            raw = _resolve_env_vars(raw or {})
            config = GatewayConfig.model_validate(raw)
            self._config = config
            self._config_hash = hashlib.sha256(
                json.dumps(raw, sort_keys=True, default=str).encode()
            ).hexdigest()[:16]
            self._loaded_at = datetime.now(UTC)
            return config

    @property
    def config(self) -> GatewayConfig:
        if self._config is None:
            self.reload()
        assert self._config is not None
        return self._config

    @property
    def config_hash(self) -> str:
        return self._config_hash

    @property
    def loaded_at(self) -> datetime | None:
        return self._loaded_at

    def find_auth_key(self, secret: str) -> AuthKey | None:
        """Look up an auth key by its secret token."""
        for key in self.config.auth.keys:
            if key.secret == secret:
                return key
        return None


def load_config(config_path: str | Path = "config.yaml", *, dotenv: bool = True) -> ConfigManager:
    """Convenience factory: optionally load .env first, then parse YAML."""
    if dotenv:
        load_dotenv()
    return ConfigManager(config_path)
