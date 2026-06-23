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

## Local dev stack

The local stack (`docker-compose.local.yml`) is used for **development and research** — this OpenCode instance uses WebGateway's `web_search` and `web_extract` MCP tools through it.

### Starting

```bash
./scripts/launch-chrome-cdp.sh                                # launch Chrome with CDP
docker compose -f docker-compose.local.yml --profile local up -d --build  # start stack
```

### Rebuilding

Source code changes (adapter edits, new providers, config changes) **require rebuilding the gateway container**:

```bash
docker compose -f docker-compose.local.yml --profile local up -d --build webgateway
```

Config-only changes (`config.local.yaml`) use hot-reload — just `POST /admin/reload`.

### Risk note

**Frequent/potentially risky commits can break the local dev stack.** If you revert or change something that affects the gateway code, the container needs to be rebuilt. If the gateway is down, this agent loses `web_search`/`web_extract` for research during development. When making risky changes, plan a rebuild after committing or keep the test stack (`docker-compose.test.yml`) as a fallback.

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
- `src/webgateway/providers/` — One file per provider adapter (searxng, jina, brave, tavily, firecrawl, duckduckgo, zyte, flaresolverr, crawl4ai, exa, perplexity, context7, devdocs, invisible_playwright)
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
- **When implementing a new provider, read `docs-src/docs/development/provider-guide.md` first.** It has the full 12-step checklist — every file to create/modify, code skeleton, test patterns, and config wiring.
- Audit entries are JSON Lines, one per request, append-only rotating file at `/app/logs/gateway.jsonl`.
- `request_id` is generated per request (format: `req_` + hex) and passed through the entire pipeline.

## Documentation

- **Always update `docs-src/` when adding or changing features.** The MkDocs site at `docs-src/docs/` is the published documentation. If you add an endpoint, provider, config option, or behavior change, update the relevant page under `docs-src/`.
- `docs-src/docs/configuration/` — config reference pages (policy engine, DLP, providers, proxy)
- `docs-src/docs/getting-started/` — first API call, installation, docker compose
- `docs-src/docs/providers/` — per-provider setup guides
- `docs-src/docs/development/` — internal guides (provider implementation checklist)
- `docs-src/docs/operations/` — cache, circuit breaker, monitoring, admin UI
- `docs-src/docs/api/` — interactive API docs, MCP schema

## Releasing

- Docker images are published to GHCR on git tag push: `git tag v0.1.0 && git push --tags`
- Workflow builds two images: `latest` (lean, 382MB) and `<tag>-injection` (full, ~1.4GB with ONNX model)
- Tags follow semver: `vMAJOR.MINOR.PATCH` — no automated tagging, tag manually when ready
- To build locally with injection: `docker build --build-arg ENABLE_INJECTION=1 -t webgateway .`

## PRD

- `PRD.md` — Original product requirements (build order in §14)
- `PRD-addendum.md` — Additional sections (S17 cache, S18 circuit breaker, S19 docs providers)
