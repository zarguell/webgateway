<p align="center">
  <img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT">
  <img src="https://img.shields.io/badge/python-3.12-blue" alt="Python 3.12">
  <img src="https://img.shields.io/badge/tests-408_passings-green" alt="408 tests">
</p>

# serpLLM

**Self-hosted, policy-driven web search and content extraction for AI agents.**

serpLLM is a single FastAPI service that abstracts web search and content extraction behind a policy-driven API. Agents call two tools — `web_search` and `web_extract` — and the gateway handles provider selection, retry logic, proxy routing, DLP enforcement, caching, and audit logging entirely below the agent layer.

```
Agent / LLM
     ↓
  POST /search  |  POST /extract  |  MCP (web_search / web_extract)
     ↓
  serpLLM  ←  policy engine, DLP, cache, provider dispatch
     ↓
   SearXNG | Jina | Brave | Tavily | Firecrawl | Exa | Context7 | DuckDuckGo | Zyte | FlareSolverr | ...
```

---

## Quick Start

```bash
# Clone and install
git clone https://github.com/zarguell/serp_llm.git
cd serp_llm

# Start the full stack
docker compose up -d --build

# Try a search
curl -X POST http://localhost:8080/search \
  -H "Authorization: Bearer $(grep AGENT1_KEY .env | cut -d= -f2)" \
  -H "Content-Type: application/json" \
  -d '{"query": "python async programming", "num_results": 3}'

# Try content extraction
curl -X POST http://localhost:8080/extract \
  -H "Authorization: Bearer $(grep AGENT1_KEY .env | cut -d= -f2)" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://docs.python.org/3/asyncio/", "format": "markdown"}'
```

---

## Features

| Capability | What it does |
|---|---|
| **15+ providers** | Self-hosted: SearXNG, Crawl4AI, InvisiblePlaywright, FlareSolverr. Cloud: Jina, Brave, Tavily, Firecrawl, Exa, Context7, Perplexity, Zyte. Free: DuckDuckGo. Opt-in: DevDocs (docs only), CDP Chrome |
| **Two-tier policy engine** | Deterministic YAML rules (domain glob, URL pattern, API key) → LLM judge on miss |
| **DLP middleware** | Outbound + inbound regex scanning, Luhn validation, redact/block/reroute actions |
| **Content pipeline** | Trafilatura extraction → markdownify conversion → boilerplate cleaning → dedup → prompt injection detection |
| **Prompt injection detection** | Layer 1: Rebuff heuristics (6 categories). Layer 2: ONNX DeBERTa classifier (704MB model, ~8ms inference). Layer 3: LLM judge escalation |
| **Response cache** | SQLite-backed, per-policy TTL rules, quality validation (length, JS blob, error page detection) |
| **Circuit breaker + quotas** | Per-provider three-state machine, monthly/daily quota tracking, quota-aware fallback reordering |
| **Alerting** | Webhook + SMTP dispatch on circuit open, quota alerts, quota exhaustion. Configurable suppression window |
| **MCP server** | Downstream `web_search` / `web_extract` tools for AI agents that speak MCP |
| **Proxy injection** | HTTP CONNECT + SOCKS5, per-request proxy via policy rules |
| **Admin UI** | Browser-based dashboard: provider health, cache ops, usage charts, key management, session viewer |
| **Audit logging** | Structured JSON Lines (rotating file) with `request_id` cross-layer tracing |
| **Session store** | Encrypted file-based cookie jar with named profiles for authenticated scraping |
| **Hot-reloadable config** | Edit `config.yaml` → `POST /admin/reload` — no rebuild needed |

---

## Configuration

Everything is driven by a single `config.yaml`:

```yaml
defaults:
  search_provider: searxng
  extract_provider: jina
  timeout: 15

policies:
  - name: reddit
    match:
      domain: "*.reddit.com"
    extract_provider: invisible_playwright
    proxy: gluetun

providers:
  searxng:
    base_url: http://searxng:8080
  jina:
    api_key: ${JINA_API_KEY}
  exa:
    api_key: ${EXA_API_KEY}
    specialization: semantic
```

Config is **hot-reloadable** — change a value and run `POST /admin/reload` with zero downtime.

---

## Architecture

```
Request → Auth → Policy Engine (YAML rules) → DLP outbound → Cache lookup
  → Proxy resolve → Provider dispatch (with fallback chain) → DLP inbound
  → Post-processing pipeline (extract → convert → clean → dedup → injection detect)
  → Cache write → Response
```

All provider adapters implement a uniform `ProviderAdapter` protocol — switching from Jina to Firecrawl to your own custom provider is a config change, not a code change.

---

## Admin UI

serpLLM includes a built-in admin dashboard at `/admin` — provider health monitoring, cache management, usage analytics, key management, session viewer, and live log streaming.

![Admin Dashboard](docs-src/docs/images/admin-dashboard.png)

> See the [Admin UI Guide](docs-src/docs/operations/admin-ui.md) for all pages and features.

---

## Commands

```bash
make install              # Create venv, install with dev deps
make lint                 # ruff check
make test-unit            # Unit tests (no Docker)
make test-integration     # Full integration flow (Docker stack)
```

---

## License

MIT

---

*Built for AI agents that need the web — without thinking about infrastructure.*
