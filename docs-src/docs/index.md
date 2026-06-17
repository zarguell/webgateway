# WebGateway

Self-hosted, Docker-native FastAPI gateway that abstracts web search and content extraction behind a single policy-driven API.

## Key Features

- **Unified API** — Single `POST /search` and `POST /extract` for all providers
- **Policy-Driven** — YAML routing rules based on domain, content type, API key, error class
- **Provider Adapters** — SearXNG, Brave, Tavily, Jina, Firecrawl, invisible_playwright, and more
- **DLP** — Regex-based data loss prevention (outbound + inbound) adapted from Gitleaks
- **Caching** — SQLite-backed response cache with TTL rules, invalidation triggers, quality gates
- **Circuit Breaker** — Per-provider failure tracking with configurable thresholds
- **Quota Management** — Monthly and daily limits per provider with alerting
- **Session Management** — Encrypted cookie jar files for authenticated browsing
- **Admin UI** — Browser-based management at `/admin` (Jinja2 + HTMX)
- **MCP Support** — Downstream MCP server for AI agent integration

## Quick Start

```bash
cp .env.example .env
# Edit .env with your API keys
docker compose up -d
```

Then visit `http://localhost:8080` for the docs, or start with your first API call:

```bash
curl -X POST http://localhost:8080/search \
  -H "Authorization: Bearer $AGENT1_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query": "latest AI news", "num_results": 5}'
```

## Architecture

```
Request → Auth → Policy Engine → DLP outbound → Cache lookup
  → Proxy resolve → Provider dispatch (with fallback) → DLP inbound
  → Cache write → Response
```

## License

MIT
