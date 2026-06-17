# AGENTS.md — WebGateway

## What this is

Self-hosted, Docker-native FastAPI gateway that abstracts web search and content extraction behind a single policy-driven API. Providers, proxies, DLP, caching, and retry logic are all handled here so AI agents never deal with infrastructure decisions.

## Naming convention (hard constraint)

Documentation may say "scrape", but **tool calls and API endpoints always use `extract`** — `POST /extract`, `web_extract` (MCP), `ExtractRequest`, `ExtractResponse`. Never use "scrape" in code.

## Commands

```bash
# Setup
make install                          # creates .venv, installs with dev deps

# Lint (must pass before commit)
make lint                             # ruff check src/ tests/
source .venv/bin/activate && ruff check src/webgateway/   # lint a subset

# Unit tests (no Docker needed)
make test-unit                        # pytest tests/ --ignore=tests/integration
source .venv/bin/activate && pytest tests/unit/ -v        # just unit

# Integration tests (requires Docker stack running)
docker compose -f docker-compose.test.yml up -d --build   # start gateway + SearXNG
source .venv/bin/activate && pytest tests/integration/ -v # run from HOST, not inside Docker
docker compose -f docker-compose.test.yml down -v         # teardown

# Full integration flow (starts stack, tests, tears down)
make test-integration

# Self-hosted Firecrawl integration tests (7 containers, ~90s startup)
make test-integration-firecrawl
```

## Architecture

```
Request → Auth → Policy Engine (YAML rules) → DLP outbound → Cache lookup
  → Proxy resolve → Provider dispatch (with fallback chain) → DLP inbound
  → Cache write → Response
```

- `src/webgateway/main.py` — App factory, lifespan, exception handlers
- `src/webgateway/service.py` — `GatewayService` orchestrates the full pipeline; all DLP/cache/provider integration lives here
- `src/webgateway/config.py` — Pydantic config models, hot-reloadable via `POST /admin/reload`
- `src/webgateway/policy/engine.py` — Tier 1 deterministic YAML rule matcher
- `src/webgateway/providers/` — One file per provider adapter (searxng, jina, brave, tavily, firecrawl)
- `src/webgateway/dlp/` — Regex scanner, Luhn validator, outbound/inbound middleware
- `src/webgateway/cache/` — SQLite cache store, key derivation, TTL rules, quality validator
- `src/webgateway/routes/` — Thin FastAPI route handlers (search, extract, health, admin, cache)

## Config files

- `config.yaml` — Production config (used by `docker-compose.yml`)
- `config.test.yaml` — Test config, volume-mounted as `/app/config.yaml` in `docker-compose.test.yml`
- Config changes are **hot-reloaded** — no rebuild needed. Source code changes **require** `docker compose up -d --build`.
- `${ENV_VAR}` and `${ENV_VAR:-default}` syntax supported in YAML values.

## Docker / testing quirks

- **Host Python is 3.14, Docker Python is 3.12.** Tests run from the host venv against the Docker-hosted gateway at `localhost:8080`.
- Integration tests use **synchronous `httpx.Client`**, not async.
- `conftest.py` auto-inserts rate-limit delays before cloud provider tests (Brave 1.2s, Tavily 0.5s, Firecrawl 0.5s, Jina 0.3s).
- Provider tests auto-skip when the provider isn't healthy (checked via `/health` endpoint). API keys are optional — tests skip gracefully.
- Health check is cached 30s in `ProviderRegistry` to avoid hammering rate-limited APIs.
- Self-hosted Firecrawl requires `NUQ_DATABASE_URL` env var pointing to the `nuq-postgres` container (skips Docker-in-Docker Postgres setup).

## Ruff

- Line length: **100 chars**
- Rules: E, F, I, UP, B, SIM
- **SIM103 can invert boolean logic** — it suggests `return condition` when you have `if condition: return True; return False`, but this is wrong for match-like functions that return `False` from within a loop. Suppress with `# noqa: SIM103` where needed.

## DLP patterns

Default regex patterns adapted from [Gitleaks](https://github.com/gitleaks/gitleaks) (MIT) and [secrets-patterns-db](https://github.com/mazen160/secrets-patterns-db) (CC BY 4.0). See `docs/adr/001-dlp-regex-only.md` for the decision to use pure regex over Presidio.

## Conventions

- Async throughout (`httpx`, `asyncio`). Provider adapters implement `async def search()` and `async def extract()`.
- Provider adapter protocol: `src/webgateway/providers/base.py` defines `ProviderAdapter`, `SearchOptions`, `ExtractOptions`, `SearchResult`, `ExtractResult`, `ProviderError`.
- Audit entries are JSON Lines, one per request, append-only rotating file at `/app/logs/gateway.jsonl`.
- `request_id` is generated per request (format: `req_` + hex) and passed through the entire pipeline.

## PRD

- `PRD.md` — Original product requirements (build order in §14)
- `PRD-addendum.md` — Additional sections (S17 cache, S18 circuit breaker, S19 docs providers)
