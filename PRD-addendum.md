# WebGateway PRD Addendum

**Version:** 0.3-addendum
**Date:** 2026-06-17
**Supplements:** PRD v0.1 dated 2026-06-16
**Supersedes:** Addendum v0.2 dated 2026-06-17
**Status:** Pre-development

***

## Section 17 — Response Cache

### 17.1 Purpose

Eliminate redundant provider calls within and across sessions. Reduces cost, proxy bandwidth, rate limit exposure, and retry storm risk. A cached gateway response means the agent's repeat calls never reach a provider at all.

### 17.2 Backend

```
v1: SQLite       — zero infra, single volume mount, survives restarts, good to ~1k req/min
v2: Redis        — add when running multiple gateway replicas (out of scope v1)
```

### 17.3 Cache Key Scheme

- **Scrape:** `hash(url + format + session_profile)`
- **Search:** `hash(query + provider + num_results)`

### 17.4 Per-Policy TTL Rules

TTL is configured per policy rule, not globally. First matching rule wins:

```yaml
cache:
  backend: sqlite
  default_ttl: 300

  rules:
    - match:
        provider: [context7, devdocs]
      ttl: 86400                        # 24h — docs stable

    - match:
        domain_glob: ["*.wikipedia.org", "*.github.com/*/README*"]
      ttl: 43200                        # 12h

    - match:
        domain_glob: ["*.reuters.com", "*.apnews.com"]
      ttl: 300                          # 5 min — news

    - match:
        domain_glob: ["*.bloomberg.com", "*.wsj.com"]
      ttl: 900                          # 15 min — financial

    - match:
        content_type: search
        provider: searxng
      ttl: 600

    - match:
        content_type: search
        provider: [brave, tavily]
      ttl: 120

    - match:
        url_pattern: ".*/(about|contact|pricing|homepage).*"
      ttl: 3600
```

### 17.5 Request-Level Cache Control

Agents can override cache behavior per-request via the request body:

```json
POST /scrape
{
  "url": "https://...",
  "format": "markdown",
  "cache": {
    "read": false,          // bypass cache lookup, always fetch fresh
    "write": false,         // don't store result (e.g. session-authed content)
    "ttl_override": 60      // store but expire faster than policy default
  }
}
```

### 17.6 Content Quality Validation

Sits between provider response and cache write. Failures trigger invalidation and fallback — error responses are never cached:

```yaml
cache:
  invalidation_triggers:
    - condition: content_length_bytes < 500
      action: invalidate_and_retry
    - condition: content_contains: ["<script>", "window.__", "Please enable JavaScript", "Are you a robot", "Access Denied"]
      action: invalidate_and_retry_next_provider
    - condition: provider_error_class: [403, 429, "bot_detected", "timeout"]
      action: invalidate
```

Quality validation pipeline:

```
Provider response
      ↓
Quality validator (length, JS blob, error page, encoding)
      ↓
  Pass → normalize → cache write → return to agent
  Fail → invalidate existing cache entry
       → increment provider failure count
       → retry next provider in fallback chain
       → if no fallback: return with quality_warning: true
```

Quality failure feeds the LLM judge's prior attempt context on retry — judge knows "jina returned a JS blob for this domain" and avoids it in subsequent routing decisions within the session.

### 17.7 Optional Cache-Control Header Passthrough

```yaml
cache:
  honor_cache_control_headers: false    # use site's Cache-Control max-age if present
  honor_etag: false                     # send If-None-Match on re-fetch, cache 304s
  policy_ttl_wins_if_shorter: true      # policy TTL caps site's stated max-age
```

### 17.8 Admin Cache Endpoints

```
POST /admin/cache/invalidate
  Body: { "url": "https://..." }               // single URL
        { "url_pattern": "*.wsj.com" }         // domain pattern
        { "provider": "jina" }                 // all entries from a provider

POST /admin/cache/flush                        // full wipe

GET  /admin/cache/stats
  Returns: { total_entries, size_bytes, hit_rate_24h, top_domains }
```

### 17.9 Normalized Response Fields (additions to PRD Section 4.5)

```json
{
  "cached": true,
  "cache_age_seconds": 142,
  "quality_warning": false
}
```

### 17.10 Audit Log Fields (additions to PRD Section 4.7)

```json
{
  "cache_hit": true,
  "quality_check_passed": true,
  "cache_invalidated": false
}
```

***

## Section 18 — Provider Resource Management

### 18.1 Purpose

Track provider consumption, protect finite quotas from exhaustion, automatically remove degraded providers from the routing pool, and dynamically re-order fallback chains based on current quota state.

### 18.2 Circuit Breaker

Per-provider three-state machine: `CLOSED → OPEN → HALF-OPEN → CLOSED`.

- **CLOSED:** normal routing
- **OPEN:** provider skipped, fallback chain used, no judge invocation needed
- **HALF-OPEN:** one trial request after cooldown; success closes, failure reopens

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
```

Circuit state surfaces in `GET /health` and `events.jsonl`.

### 18.3 Quota Tracking

Hard limits tracked in SQLite, persisted across restarts. Quota state loaded from DB on startup — never resets to zero on restart.

```yaml
quotas:
  exa:
    monthly_limit: 100
    alert_at_percent: 80
    exhausted_action: remove_from_pool   # or: fallback_only
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

`remove_from_pool` — provider unavailable until reset day.
`fallback_only` — used only when all others in chain fail; preserves remaining quota.

### 18.4 Usage Tracking Schema

```sql
CREATE TABLE provider_usage (
  id          INTEGER PRIMARY KEY,
  ts          DATETIME,
  provider    TEXT,
  operation   TEXT,        -- 'search' | 'scrape'
  request_id  TEXT,
  api_key_id  TEXT,
  success     BOOLEAN,
  latency_ms  INTEGER,
  error_class TEXT,
  cost_units  REAL
);
```

### 18.5 Normalized Cost Units

Relative unit for comparing consumption across providers — not real currency:

```yaml
providers:
  exa:
    cost_units_per_call: 1.0
  firecrawl:
    cost_units_per_call: 0.5
  jina:
    cost_units_per_call: 0.1
  searxng:
    cost_units_per_call: 0.0
  crawl4ai:
    cost_units_per_call: 0.0
  context7:
    cost_units_per_call: 0.0
```

### 18.6 Quota-Aware Dynamic Routing

Policy engine re-orders fallback chains at runtime based on quota consumption and cost units. Agent-facing policy config is unchanged — routing is silently optimized:

```
Configured chain:  [exa, brave, searxng]

Runtime state:
  exa:     87/100 used (87%) → deprioritize
  brave:   45/2000 used (2%) → preferred
  searxng: unlimited          → always available

Effective order:   [brave, searxng, exa]
```

Quota-aware re-ordering does not override an explicit LLM judge decision above the confidence threshold.

### 18.7 Alerting

```json
{"ts": "...", "event": "quota_alert", "provider": "exa", "pct_used": 80, "remaining": 20}
{"ts": "...", "event": "circuit_open", "provider": "zyte", "trigger": "429", "cooldown_seconds": 300}
{"ts": "...", "event": "circuit_closed", "provider": "zyte"}
{"ts": "...", "event": "quota_exhausted", "provider": "exa", "action": "remove_from_pool"}
```

Optional webhook:

```yaml
alerts:
  webhook_url: ${ALERT_WEBHOOK_URL}    # Slack, Discord, ntfy, generic POST
  events: [quota_alert, circuit_open, quota_exhausted]
```

### 18.8 Admin Endpoints

```
GET  /admin/usage/summary
  Returns: per-provider { calls_today, calls_month, quota_remaining,
           quota_pct, circuit_state, cost_units_today }

GET  /admin/usage/history?provider=exa&days=30
  Returns: daily call counts, error rates, latency p50/p95

POST /admin/quota/reset?provider=exa
POST /admin/quota/override
  Body: { "provider": "exa", "remaining": 50 }

POST /admin/circuit/reset?provider=zyte
```

***

## Section 19 — Docs Providers as Search Providers

### 19.1 Principle

Docs providers are first-class search providers — not a separate operation type or endpoint. They answer "find me information about X" via `POST /search`, the same as Brave or Tavily. The distinction is specialization scope, not interface. No `/docs` endpoint. No `docs_lookup` MCP tool.

### 19.2 Provider Config Shape

`specialization` is metadata used by the LLM judge and policy rules for routing decisions. It does not change the adapter interface:

```yaml
providers:
  context7:
    type: search
    mcp_native: true
    cost_units_per_call: 0.0
    specialization: docs

  devdocs:
    type: search
    base_url: http://devdocs:9292
    self_hosted: true
    cost_units_per_call: 0.0
    specialization: docs

  exa:
    type: search
    specialization: semantic

  searxng:
    type: search
    specialization: general
```

### 19.3 Routing

**Tier 1 — URL pattern rules for known docs domains** (scrape path):

```yaml
- name: docs_domains
  match:
    domain_glob: ["docs.github.com", "*.readthedocs.io", "pkg.go.dev",
                  "developer.mozilla.org", "docs.python.org"]
  scrape_provider: jina
  cache_ttl: 86400
```

**Tier 2 — LLM judge** routes to docs providers on policy miss when query characteristics suggest library/API/spec lookup. Decision is visible in audit log via `reasoning_tag: "library_docs_lookup"` — no magic, fully transparent.

**Tier 1 explicit rules** for known libraries (operator-configured, optional):

```yaml
- name: library_docs
  match:
    query_pattern: "\\b(langchain|openai|fastapi|pydantic|numpy)\\b"
  search_provider: context7
  fallback_chain: [context7, exa, searxng]
```

### 19.4 Updated Search Provider Table (replaces PRD Section 6)

| Provider | Specialization | Self-hosted | MCP-native | Notes |
|---|---|---|---|---|
| SearXNG | General | ✅ | ❌ | Default self-hosted |
| DuckDuckGo | General | ✅ (no key) | ❌ | Rate-limited |
| Brave Search API | General | ❌ | ❌ | Fast, agent-optimized |
| Tavily | Agentic/RAG | ❌ | ❌ | Built for agent workflows |
| Exa | Semantic | ❌ | ❌ | Best for similarity search |
| Perplexity API | AI-native | ❌ | ❌ | Returns summarized answers |
| Linkup | Agentic | ❌ | ❌ | Real-time focused |
| SerpAPI / ValueSERP | Google proxy | ❌ | ❌ | When Google results required |
| Grok Search | AI-native | ❌ | ❌ | |
| Gemini Search | AI-native | ❌ | ❌ | |
| **Context7** | **Docs** | **❌** | **✅** | Library docs, versioned; first MCP upstream adapter to implement |
| **DevDocs** | **Docs** | **✅ (Docker)** | **❌** | 100+ official docs aggregated |

### 19.5 Caching

Docs providers carry the highest cache value in the system. Default TTL 24h per Section 17.4. A coding agent may resolve the same library docs 20+ times in one session — cache eliminates all but the first call.

***

## Full Config Schema Additions (supplements PRD Section 11)

```yaml
cache:
  backend: sqlite
  default_ttl: 300
  honor_cache_control_headers: false
  honor_etag: false
  policy_ttl_wins_if_shorter: true
  invalidation_triggers:
    - condition: content_length_bytes < 500
      action: invalidate_and_retry
    - condition: content_contains: ["<script>", "window.__", "Please enable JavaScript"]
      action: invalidate_and_retry_next_provider
    - condition: provider_error_class: [403, 429, "bot_detected"]
      action: invalidate
  rules: [...]                          # per Section 17.4

circuit_breaker:
  enabled: true
  providers:
    default:
      error_threshold: 5
      window_seconds: 60
      cooldown_seconds: 120
      trip_on: [429, 503, "timeout", "bot_detected"]

quotas:
  exa:
    monthly_limit: 100
    alert_at_percent: 80
    exhausted_action: remove_from_pool
    reset_day: 1

alerts:
  webhook_url: ${ALERT_WEBHOOK_URL}
  events: [quota_alert, circuit_open, quota_exhausted]

# Per-provider additions
providers:
  context7:
    type: search
    mcp_native: true
    specialization: docs
    cost_units_per_call: 0.0
  devdocs:
    type: search
    base_url: http://devdocs:9292
    self_hosted: true
    specialization: docs
    cost_units_per_call: 0.0
  exa:
    api_key: ${EXA_API_KEY}
    specialization: semantic
    cost_units_per_call: 1.0
  searxng:
    base_url: http://searxng:8080
    specialization: general
    cost_units_per_call: 0.0
```
