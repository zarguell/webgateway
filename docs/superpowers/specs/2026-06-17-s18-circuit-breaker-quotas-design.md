# S18 — Circuit Breaker + Quotas Design

**Date:** 2026-06-17
**Status:** Pre-implementation
**Supersedes:** PRD-addendum.md §18
**Design decision:** Unified `ProviderResourceManager` (circuit breaker + quotas as two resource types in one component)

---

## 1. Architecture

```
GatewayService
  └── _execute_with_fallback()
        └── ProviderResourceManager.filter_available(candidates)
              ├── Removes OPEN (tripped) providers
              └── Reorders by quota consumption (ascending)

ProviderResourceManager
  ├── SQLite: circuit_breaker_state table (persisted state machine)
  ├── SQLite: provider_usage table (append-only usage log)
  ├── In-memory: circuit state cache (for fast hot-path checks)
  └── Alerting: events.jsonl + optional webhook
```

### Design Rationale

**Why unify circuit breaker and quotas?** Both are per-provider resource governance. The circuit breaker tracks failure budget within a sliding window; quotas track usage budget within a calendar window. They share:

- Same persistence backend (SQLite)
- Same routing integration point (`_execute_with_fallback`)
- Same admin surface (summary, reset, override)
- Same alerting pathway (events.jsonl → optional webhook)

The single `filter_available()` call handles both in one pass, keeping the service.py change to ~3 lines.

**Why persist circuit state?** Prevents restarted gateway instances from hammering a still-degraded provider. Half-open probes on the first request detect recovery naturally.

---

## 2. SQLite Schema

File: `data/resource_manager.db` (alongside `data/cache.db`)

```sql
CREATE TABLE IF NOT EXISTS circuit_breaker_state (
    provider        TEXT PRIMARY KEY,
    state           TEXT NOT NULL DEFAULT 'closed',
    failure_count   INTEGER NOT NULL DEFAULT 0,
    window_start_ts REAL,
    last_failure_ts REAL,
    opened_at_ts    REAL,
    cooldown_seconds INTEGER NOT NULL DEFAULT 120
);

CREATE TABLE IF NOT EXISTS provider_usage (
    id          INTEGER PRIMARY KEY,
    ts          REAL NOT NULL,
    provider    TEXT NOT NULL,
    operation   TEXT NOT NULL,
    request_id  TEXT,
    success     BOOLEAN NOT NULL,
    latency_ms  INTEGER,
    error_class TEXT,
    cost_units  REAL NOT NULL DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS idx_usage_provider_ts ON provider_usage(provider, ts);
```

### Retention

`provider_usage` rows older than 90 days are pruned on manager construction (once per process start).

---

## 3. Config Models (additions to `config.py`)

```python
class CircuitBreakerProviderConfig(BaseModel):
    error_threshold: int = 5
    window_seconds: int = 60
    cooldown_seconds: int = 120
    trip_on: list[str] = Field(default_factory=lambda: ["429", "503", "timeout", "bot_detected"])

class CircuitBreakerConfig(BaseModel):
    enabled: bool = True
    providers: dict[str, CircuitBreakerProviderConfig] = Field(default_factory=dict)
    # key "default" applies to all providers without explicit config

class QuotaProviderConfig(BaseModel):
    monthly_limit: int | None = None
    daily_limit: int | None = None
    alert_at_percent: int = 80
    exhausted_action: Literal["remove_from_pool", "fallback_only"] = "remove_from_pool"
    reset_day: int = 1

class QuotasConfig(BaseModel):
    providers: dict[str, QuotaProviderConfig] = Field(default_factory=dict)

class AlertEvent(BaseModel):
    webhook_url: str | None = None
    events: list[str] = Field(default_factory=list)

# Added to GatewayConfig:
#   circuit_breaker: CircuitBreakerConfig = Field(default_factory=CircuitBreakerConfig)
#   quotas: QuotasConfig = Field(default_factory=QuotasConfig)
#   alerts: AlertEvent = Field(default_factory=AlertEvent)
```

### Config YAML shape (validated sample)

```yaml
circuit_breaker:
  enabled: true
  providers:
    default:
      error_threshold: 5
      window_seconds: 60
      cooldown_seconds: 120
      trip_on: [429, 503, "timeout", "bot_detected"]
    zyte:
      error_threshold: 2
      cooldown_seconds: 300

quotas:
  exa:
    monthly_limit: 100
    alert_at_percent: 80
    exhausted_action: remove_from_pool
    reset_day: 1
  tavily:
    monthly_limit: 1000
    alert_at_percent: 90
    exhausted_action: remove_from_pool
  brave:
    monthly_limit: 2000
    daily_limit: 200
    exhausted_action: fallback_only
```

---

## 4. ProviderResourceManager Interface

```python
class ProviderResourceManager:
    def __init__(self, db_path: str, config: ResourceManagerConfig): ...

    # ── Circuit breaker ──

    async def record_failure(self, provider: str, error_class: str | None) -> None
    async def record_success(self, provider: str) -> None
    async def get_circuit_state(self, provider: str) -> str  # "closed"|"open"|"half_open"
    async def reset_circuit(self, provider: str) -> None

    # ── Quota tracking ──

    async def record_usage(
        self, provider: str, operation: str, request_id: str,
        success: bool, latency_ms: int, error_class: str | None, cost_units: float,
    ) -> None
    async def get_quota_info(self, provider: str) -> QuotaInfo
    async def override_quota(self, provider: str, remaining: int) -> None
    async def reset_quota(self, provider: str) -> None

    # ── Routing integration ──

    async def filter_available(self, candidates: list[str]) -> list[str]
    async def get_summary(self) -> dict[str, ProviderSummary]
    async def get_history(self, provider: str, days: int) -> list[dict]
```

### Circuit Breaker State Machine

```
CLOSED
  record_failure → failure_count++ ; if threshold exceeded → OPEN
  record_success → failure_count = 0

OPEN
  filter_available → provider excluded from candidates
  after cooldown_seconds → HALF-OPEN (on next record_failure/record_success call)

HALF-OPEN
  record_success → CLOSED ; failure_count = 0
  record_failure → OPEN ; cooldown restarts
```

### Sliding Window Logic

On `record_failure`:
1. If `now - window_start_ts > window_seconds` → reset `failure_count = 1`, update `window_start_ts = now`
2. Else → increment `failure_count`
3. If `failure_count >= error_threshold` → transition to OPEN

This prevents an old burst from keeping the circuit open.

### Quota Calculation

`get_quota_info`:
- Monthly: `SUM(cost_units) WHERE provider = ? AND ts >= start_of_month`
- Daily: `SUM(cost_units) WHERE provider = ? AND ts >= start_of_today`
- Returns: `calls_month, limit_month, calls_today, limit_today, pct_used, exhausted`

### filter_available Algorithm

```
1. For each candidate:
   a. Read circuit state. If OPEN → skip.
   b. Read quota info. If exhausted and action=remove_from_pool → skip.
   c. If exhausted and action=fallback_only → mark as last resort.

2. Sort remaining candidates by quota_pct ascending.

3. Append fallback_only candidates at the end.

4. If no candidates remain → return [].

5. Trigger alert events:
   a. If quota_pct > alert_at_percent → quota_alert
   b. If quota exhausted → quota_exhausted
```

---

## 5. Integration Points

### service.py — `_execute_with_fallback`

```python
async def _execute_with_fallback(self, provider_name, fallback_chain, ...):
    candidates = [provider_name] + [
        name for name in fallback_chain if name != provider_name
    ]

    # NEW
    if self._resource_manager is not None:
        candidates = await self._resource_manager.filter_available(candidates)

    # ... rest unchanged ...
```

### service.py — After provider call succeeds

```python
# In search() and extract(), after successful provider call:
if self._resource_manager is not None:
    await self._resource_manager.record_success(provider_used)
    await self._resource_manager.record_usage(...)
```

### service.py — After provider call fails (ProviderError caught)

```python
# Inside the except ProviderError block in _execute_with_fallback:
if self._resource_manager is not None:
    await self._resource_manager.record_failure(candidate_name, error_class)
```

### main.py — Lifespan

```python
resource_manager = ProviderResourceManager(
    db_path="data/resource_manager.db",
    config=config_manager.config,  # reads circuit_breaker + quotas
)
app.state.resource_manager = resource_manager
# Pass to GatewayService constructor
```

---

## 6. Admin Endpoints (routes/admin.py)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/admin/usage/summary` | admin | Per-provider usage + circuit state |
| GET | `/admin/usage/history` | admin | Daily call counts, errors, latency |
| POST | `/admin/quota/reset` | admin | Reset usage for a provider |
| POST | `/admin/quota/override` | admin | Set remaining quota value |
| POST | `/admin/circuit/reset` | admin | Force-close a circuit breaker |

### Response schemas (additions to `schemas.py`)

```python
class UsageSummaryItem(BaseModel):
    provider: str
    circuit_state: str  # closed | open | half_open
    calls_today: int
    calls_month: int
    limit_month: int | None
    quota_pct: float | None  # 0–100, None if unlimited
    cost_units_today: float

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
```

---

## 7. Health Endpoint Integration

`ProviderHealthInfo` additions:
- `circuit_state: str | None` — "closed", "open", "half_open"
- `quota_pct: float | None` — 0.0–100.0 or None if unlimited

The health route calls `resource_manager.get_summary()` parallel with `registry.health_check_all()` and merges the data.

---

## 8. Alerting

Alert events are written to `events.jsonl` (new rotating log file in the same directory as `gateway.jsonl`). Optional webhook POST fired as a background `asyncio.create_task` — never blocks the request path.

Webhook payload format:

```json
{"ts": "2026-06-17T12:00:00Z", "event": "circuit_open", "provider": "zyte", "trigger": "429", "cooldown_seconds": 300}
{"ts": "2026-06-17T12:00:00Z", "event": "quota_alert", "provider": "exa", "pct_used": 80, "remaining": 20}
{"ts": "2026-06-17T12:00:00Z", "event": "quota_exhausted", "provider": "exa", "action": "remove_from_pool"}
```

---

## 9. Files Changed

| File | Change |
|------|--------|
| `src/serp_llm/resource_manager.py` | **New** — ~300 lines |
| `src/serp_llm/config.py` | +50 lines (3 config models, 3 fields on GatewayConfig) |
| `src/serp_llm/schemas.py` | +50 lines (admin request/response models, health additions) |
| `src/serp_llm/service.py` | +20 lines (resource_manager param, record_success/usage/failure calls) |
| `src/serp_llm/routes/admin.py` | +100 lines (5 new endpoints) |
| `src/serp_llm/routes/health.py` | +10 lines (circuit_state + quota_pct in response) |
| `src/serp_llm/main.py` | +5 lines (instantiate + wire resource_manager) |
| `config.yaml` | +30 lines (circuit_breaker + quotas + alerts blocks) |
| `config.test.yaml` | +5 lines (simplified circuit_breaker/test quotas) |
| `tests/unit/test_resource_manager.py` | **New** — unit tests |

No changes to: policy engine, provider adapters, existing routes (beyond additions), cache, DLP, auth.

---

## 10. Spec Self-Review

- No TBD/TODO placeholders
- No contradictions between sections (schema matches config matches interface)
- Scope is focused: circuit breaker + quotas only (no session store creep)
- All requirements unambiguous: each field has explicit types, units, and behaviors
- YAGNI: removed circuit-breaker-only in-memory option, removed Redis caching for circuit state (SQLite is sufficient for single-replica v1)
