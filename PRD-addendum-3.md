# serpLLM PRD Addendum v0.5 (final)

**Date:** 2026-06-17
**Supplements:** PRD v0.1 + Addenda v0.3, v0.4
**Supersedes:** Addendum v0.5 draft
**Status:** Pre-development

***

## Section 22 — Admin UI (`/admin`)

### 22.1 Purpose

A lightweight, self-contained web UI for operators to manage API keys, monitor provider health, inspect usage stats, and tail logs — without requiring direct config file or database access. No external frontend framework dependencies; shipped as static files served by the FastAPI process itself.

### 22.2 Technology Choice

**Server-side rendered HTML + HTMX.** No React, no Vue, no build step, no `node_modules`. Jinja2 templates rendered by FastAPI, HTMX for dynamic updates (live log tail, stats refresh) without writing JavaScript. TailwindCSS via CDN for styling. The entire admin UI is a handful of HTML templates baked into the gateway image — no separate frontend container, no build pipeline.

Rationale: this is an operator tool, not a product UI. It needs to work reliably, be trivially maintainable, and add zero deployment complexity.

### 22.3 Authentication

Admin UI uses the same Bearer token system as the REST API. Browser access via a session cookie issued on login:

```
GET  /admin/login  → login form
POST /admin/login
  Body: { api_key: <admin_key> }
  → validates key hash, issues httpOnly session cookie (24h TTL)
  → redirects to /admin/dashboard
GET  /admin/logout → clears cookie, redirects to /admin/login
```

Only keys with `role: admin` in the key store can access `/admin/*`. Operator-role keys are rejected at the login form with a clear message.

### 22.4 Roles

Two roles stored per key in the `api_keys` SQLite table:

| Role | REST API Access | Admin UI Access |
|---|---|---|
| `operator` | `POST /search`, `POST /scrape` | ❌ |
| `admin` | All REST endpoints + `/admin/*` | ✅ Full UI |

No further RBAC granularity in v1 — full RBAC deferred to LiteLLM layer above.

### 22.5 UI Pages

#### `/admin/dashboard`
At-a-glance status overview:
- Provider health grid — each provider as a card: name, status (healthy / degraded / circuit-open / quota-exhausted), last check timestamp, calls today, quota remaining %
- Request volume sparkline — last 24h, search vs scrape
- Cache hit rate — last 24h
- Active alerts — from `events.jsonl`, last 10 unresolved

#### `/admin/keys`
API key management:
- List all keys — id, label, role, secret prefix (first 8 chars only), created date, last used, call count today/month, revoked status
- **Create new key** — generates a cryptographically random secret, displays plaintext exactly once with a "Copy now — this will never be shown again" warning, stores only the bcrypt hash
- **Revoke key** — immediate effect, no restart required, sets `revoked: true` in DB
- Secret values never displayed after initial creation — not in list, not in status, not in any API response

#### `/admin/providers`
Provider status and metadata:
- Full provider table: health, circuit state, quota %, cost units today, specialization
- `warnings` field displayed as inline alerts (e.g. Camoufox degradation notices)
- Manual circuit reset button per provider
- Manual quota override form per provider

#### `/admin/sessions`
Cookie Bucket management:
- List all sessions — id, domain, browser, expiry, last used, status (valid / expired / proxy-unbound)
- Create session — form for manual Firefox cookie import
- Invalidate session — single, by domain, or by browser service
- Refresh session — re-import updated cookies
- Cookie values never displayed — write-only after creation

#### `/admin/usage`
Usage stats:
- Per-provider table: calls today/month, error rate, p50/p95 latency, cost units
- Per-key table: calls today/month, top domains, top providers used
- Time range selector: 24h / 7d / 30d
- Sourced from `provider_usage` SQLite table — no external analytics service

#### `/admin/logs`
Live log viewer:
- Tail of `gateway.jsonl` — last 100 entries, auto-refreshes every 5s via HTMX polling
- Filter by: api_key_id, provider, status (success/error), cache_hit, session_profile
- Each log entry expandable to full JSON
- `events.jsonl` alert log in a separate tab — circuit trips, quota alerts, session invalidations

#### `/admin/cache`
Cache management:
- Stats: total entries, size, hit rate 24h, top cached domains
- Invalidate by URL, domain pattern, or provider — form with confirmation
- Full flush button with confirmation dialog

***

## Section 23 — Static Docs Site (`/docs`)

### 23.1 Purpose

Human-readable documentation for operators and agent developers. Covers configuration, providers, policies, session management, and operations. Served statically from the gateway process — no external hosting required.

### 23.2 Technology Choice

**MkDocs** (Material theme) compiled to static HTML at image build time. Output served by FastAPI as static files at `/docs`. No separate docs container. Source in `/docs-src`, compiled output in `/static/docs`.

```dockerfile
RUN pip install mkdocs mkdocs-material && \
    mkdocs build --site-dir /app/static/docs
```

### 23.3 Content Structure

```
/docs
  Getting Started
    Installation
    Docker Compose quickstart
    First API call
    Bootstrap admin key setup
  Configuration
    config.yaml reference (full schema, no secrets)
    .env reference (all secret keys)
    Provider setup guides (per provider)
    Policy engine — rule syntax and examples
    DLP policy configuration
    Proxy configuration + Gluetun setup
  Providers
    Search providers
    Scrape providers
    Stealth browsers (invisible_playwright vs Camoufox)
    Provider data policies (auto-generated from ProviderMetadata)
  Sessions & Authentication
    Cookie Bucket setup and usage
    Session lifecycle
    API key management
    Bootstrap key and first-run setup
  Operations
    Admin UI guide
    Monitoring and alerting
    Cache management
    Circuit breaker behavior
    Quota management
  API Reference
    (links to /api/docs for interactive spec)
  Architecture
    System design overview
    LiteLLM integration boundary
    Docker Compose profiles
```

### 23.4 Provider Data Policy Pages

Each provider gets a dedicated page auto-generated from its `ProviderMetadata` at build time — GDPR compliance, data retention, training policy, data residency, privacy policy link. Satisfies the PRD v0.1 goal of exposing provider data policies to operators.

***

## Section 24 — OpenAPI Spec & Interactive UI (`/api/docs`)

### 24.1 Purpose

Auto-generated, always-accurate API reference. FastAPI generates the OpenAPI 3.1 spec from code — no manual spec maintenance. Any drift between docs and implementation is impossible.

### 24.2 Endpoints

```
GET /api/docs          → Swagger UI (interactive, try-it-out enabled)
GET /api/redoc         → ReDoc UI (cleaner read-only reference)
GET /api/openapi.json  → raw OpenAPI 3.1 JSON spec
GET /mcp/schema        → MCP tool definitions (linked from Swagger UI description)
```

All served by FastAPI natively — zero additional dependencies.

### 24.3 Spec Quality Requirements

All route handlers must include:
- `summary` — one-line description
- `description` — full markdown, including policy behavior notes
- `response_model` — typed Pydantic v2 model (drives schema generation)
- `tags` — `["search"]`, `["scrape"]`, `["admin"]`, `["sessions"]`, `["keys"]`
- Example request/response bodies via Pydantic `model_config`

Pydantic v2 models serve double duty: runtime validation and OpenAPI schema generation. No schema divergence possible.

### 24.4 Auth in Swagger UI

```python
from fastapi.security import HTTPBearer
security = HTTPBearer()
```

Applied globally — Swagger UI shows an "Authorize" button. Operator pastes their API key; all try-it-out calls include the `Authorization: Bearer` header automatically.

***

## Section 25 — API Key Storage & Bootstrap

### 25.1 Keys Live in SQLite, Not Config

API keys are operational data with a lifecycle — they are created, used, rotated, and revoked at runtime. They do not belong in `config.yaml`. Config is for routing behavior and is safe to commit to version control. Keys are secrets and must never appear in any file that could be committed, copied into a Docker image layer, or shared.

### 25.2 Key Storage Schema

```sql
CREATE TABLE api_keys (
  id            TEXT PRIMARY KEY,       -- e.g. "key_abc123", human-readable
  secret_hash   TEXT NOT NULL,          -- bcrypt hash only, plaintext never stored
  label         TEXT,                   -- human description
  role          TEXT NOT NULL,          -- 'operator' | 'admin'
  created_ts    DATETIME NOT NULL,
  last_used_ts  DATETIME,
  revoked       BOOLEAN DEFAULT FALSE,
  revoked_ts    DATETIME
);
```

Plaintext secret is generated once on key creation, returned to the caller exactly once, then discarded. Only the bcrypt hash is persisted.

### 25.3 Bootstrap Key

On first startup with an empty `api_keys` table the gateway cannot authenticate any request — including the request needed to create the first admin key. The bootstrap key solves this:

```
# .env
BOOTSTRAP_ADMIN_KEY=<cryptographically random secret, generated by operator>
```

Behavior:
- If `BOOTSTRAP_ADMIN_KEY` is set in environment AND `api_keys` table is empty, this key is accepted as a valid admin credential
- It is **never written to the database** — it exists only in memory for the duration of the process
- Once any admin key has been created via the UI or API, the bootstrap key is rejected even if still set in environment — the table is no longer empty
- Operator should unset `BOOTSTRAP_ADMIN_KEY` from `.env` after creating their first real admin key
- Bootstrap key usage is always written to the audit log with `api_key_id: "bootstrap"` for traceability

First-run sequence:
```
1. Operator sets BOOTSTRAP_ADMIN_KEY=<secret> in .env
2. docker compose up
3. Operator hits POST /admin/login with the bootstrap key
4. Operator creates a real admin key via /admin/keys
5. Operator copies the plaintext secret (shown once)
6. Operator removes BOOTSTRAP_ADMIN_KEY from .env
7. docker compose restart (or just leave — bootstrap is now inert)
```

### 25.4 Provider API Keys — Environment Only

All provider secrets live in `.env`, accessed directly from `os.environ` in adapter code. They are never interpolated into `config.yaml`:

```
# .env — gitignored, never committed
BRAVE_API_KEY=...
JINA_API_KEY=...
TAVILY_API_KEY=...
EXA_API_KEY=...
ZYTE_API_KEY=...
FIRECRAWL_API_KEY=...
SESSION_ENCRYPTION_KEY=...
ADMIN_SESSION_SECRET=...
BOOTSTRAP_ADMIN_KEY=...     # remove after first admin key created
```

`config.yaml` references providers by name only — no secrets, safe to commit:

```yaml
# config.yaml — safe to commit
providers:
  brave:
    base_url: https://api.search.brave.com
    # no api_key field here
  firecrawl:
    base_url: http://firecrawl:3002
    # no api_key field here
```

### 25.5 Key Rotation

No dedicated rotation UI in v1 — operator creates a new key, updates the calling agent's config, then revokes the old key. Both keys are valid during the transition window. Revocation is immediate and requires no restart.

***

## Complete Route Map

```
/                      → redirect to /docs
/api/docs              → Swagger UI
/api/redoc             → ReDoc UI
/api/openapi.json      → OpenAPI 3.1 spec
/docs                  → static MkDocs site (served as static files)
/mcp                   → MCP server endpoint
/mcp/schema            → MCP tool definitions JSON

# Agent-facing
POST /search
POST /scrape

# Operational REST (admin role required)
GET  /health
GET  /providers
POST /admin/reload
POST /admin/cache/invalidate
POST /admin/cache/flush
GET  /admin/cache/stats
GET  /admin/usage/summary
GET  /admin/usage/history
POST /admin/quota/reset
POST /admin/quota/override
POST /admin/circuit/reset
POST /admin/sessions/create
GET  /admin/sessions
GET  /admin/sessions/{session_id}/status
POST /admin/sessions/invalidate
POST /admin/sessions/{session_id}/refresh

# Key management REST (admin role required)
GET  /admin/keys
POST /admin/keys/create
POST /admin/keys/{key_id}/revoke

# Admin UI (admin role, session cookie)
GET  /admin/login
POST /admin/login
GET  /admin/logout
GET  /admin/dashboard
GET  /admin/keys        (UI view)
GET  /admin/providers
GET  /admin/sessions    (UI view)
GET  /admin/usage
GET  /admin/logs
GET  /admin/cache
```

***

## Docker Compose Additions

No new containers. All features served by the gateway process:

```yaml
services:
  serpllm:
    build: .
    ports:
      - "8080:8080"
    volumes:
      - ./config.yaml:/app/config.yaml
      - ./logs:/app/logs
      - ./sessions:/app/sessions
      - ./data:/app/data           # SQLite DB location (keys, usage, quota, cache)
      - ./static:/app/static       # compiled MkDocs output
    env_file:
      - .env
```

***

## Build Order Additions (appends to v0.3 + v0.4 order)

- **25.** `api_keys` SQLite table + bcrypt hash verification middleware
- **26.** Bootstrap key logic — empty-table detection, env var auth, audit log entry
- **27.** Key CRUD REST endpoints — create (return plaintext once), list, revoke
- **28.** OpenAPI spec quality pass — Pydantic v2 models, summaries, tags, examples on all routes
- **29.** `/api/docs` Swagger UI + `/api/redoc` ReDoc + `/mcp/schema`
- **30.** MkDocs site scaffold — Material theme, content structure, `.env` reference page
- **31.** Provider data policy pages auto-generated from `ProviderMetadata` at build time
- **32.** Admin UI — Jinja2 templates, HTMX, TailwindCSS CDN, httpOnly session cookie
- **33.** Admin login/logout + role enforcement + bootstrap key UI support
- **34.** Dashboard — provider health grid, sparkline, cache hit rate, alerts
- **35.** Keys page — list with secret prefix, create with one-time display, revoke
- **36.** Providers page — health, warnings, circuit reset, quota override
- **37.** Sessions page — list, create, invalidate, refresh
- **38.** Usage page — per-provider and per-key stats, time range selector
- **39.** Logs page — HTMX live tail, filter controls, expandable JSON entries
- **40.** Cache page — stats, invalidation forms, flush with confirmation

