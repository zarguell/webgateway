---
# serpLLM — Product Requirements Document
**Version:** 0.1-draft
**Date:** 2026-06-16
**Status:** Pre-development
**Authors:** Derived from design session

***
## 1. Purpose & Problem Statement
AI agents doing web research currently must manage provider selection, retry logic, proxy routing, bot detection, and content extraction themselves — or delegate these infrastructure decisions to the LLM, which wastes reasoning tokens and produces unreliable behavior. No existing tool unifies web search and web scraping behind a single, policy-driven, provider-agnostic API.

**serpLLM** is a self-hosted, Docker-native service that exposes a single REST + MCP interface for all web search and scraping operations. It abstracts provider selection, retry/fallback, proxy routing, DLP enforcement, and audit logging entirely away from the agent layer. Agents call two tools — `web_search` and `web_scrape` — and the gateway handles everything below that.

**Core principle:** Infrastructure decisions (which provider, which proxy, retry strategy) must never consume agent context or reasoning tokens.

***
## 2. Architecture Overview
The gateway is a single FastAPI service. All agents — regardless of whether they speak REST or MCP — connect to one endpoint. A two-tier policy engine routes requests: first via deterministic YAML rules, then via a lightweight LLM judge for ambiguous cases. Provider adapters are uniform internally regardless of whether they speak REST or MCP upstream. Browser services are never baked into the gateway image; they run as separate containers and are called over the Docker Compose network.

***
## 3. Design Principles
1. **Single tool surface for agents** — exactly two tools exposed: `web_search` and `web_scrape`. No provider names leak to the agent layer.
2. **Infrastructure decisions never consume agent context** — routing, retry, and provider selection happen entirely within the gateway.
3. **Slim gateway image** — no browsers, no heavy dependencies baked in. Heavy services (Playwright, FlareSolverr, Splash) are separate containers.
4. **Policy-as-config** — all routing behavior is driven by a hot-reloadable `config.yaml`. No code changes needed to swap providers or add rules.
5. **Self-hosted first** — every component has a self-hostable option. Managed providers are supported but never required.
6. **Composable with LiteLLM** — sits below LiteLLM in the stack. LiteLLM handles model/user RBAC above; serpLLM handles fetch routing and data policy below. Joined by `request_id`.
7. **Audit-ready from day one** — structured logs written from the first commit, even if enterprise RBAC is deferred.

***
## 4. System Components
### 4.1 FastAPI Gateway Service
- Single Python service, async throughout (`httpx`, `asyncio`)
- Exposes REST API and MCP server on configurable ports
- Hot-reloadable config via `POST /admin/reload` or filesystem watch
- No UI in initial release — operator interaction is config file + API only
- Stateless request handling; session/cookie state stored externally (encrypted file store)
### 4.2 Policy Engine
Two-tier routing:

**Tier 1 — Deterministic YAML rules**
Evaluated in order, first match wins. Rule criteria:
- `domain` / `domain_glob` (e.g. `*.reddit.com`)
- `url_pattern` (regex)
- `api_key_id` (route by calling agent identity)
- `content_type` (`search` | `scrape`)
- `query_contains` (substring/regex match on query text)

Per-rule actions:
- `search_provider` / `scrape_provider`
- `proxy`
- `playwright_profile` (named cookie jar)
- `fallback_chain` (ordered provider list)
- `retry_strategy`
- `dlp_policy`

**Tier 2 — LLM Judge**
Fires only on policy miss or on configurable error classes. Returns structured JSON only — no prose. Judge output: `{ provider, proxy, reasoning_tag, confidence, fallback_if_fail }`. `reasoning_tag` is logged but not acted on. Decisions are cached (configurable TTL, default 1h) keyed on URL + context hash. Below a configurable `confidence_threshold`, falls back to default provider without retrying the judge.
### 4.3 Provider Adapters
Uniform internal interface regardless of upstream protocol:

```python
class ProviderAdapter(Protocol):
    async def scrape(self, url: str, options: ScrapeOptions) -> ScrapeResult: ...
    async def search(self, query: str, options: SearchOptions) -> SearchResult: ...
```

All adapters accept an optional `proxy_url` in options, injected by the policy engine. Adapters have no knowledge of each other.
### 4.4 Proxy Injector
Injects proxy config per-request into httpx client or browser service call options. Never sets proxy globally. Proxy types supported: HTTP CONNECT, SOCKS5. Proxy identities are named in config and referenced by policy rules — adapters receive a resolved URL string only.
### 4.5 Response Normalizer
All providers return different shapes. Normalizer produces a uniform response:

**Search result:**
```json
{
  "results": [{ "title", "url", "snippet", "published_date?" }],
  "provider_used": "searxng",
  "request_id": "req_8f3a2c",
  "latency_ms": 310
}
```

**Scrape result:**
```json
{
  "content": "...",
  "format": "markdown",
  "url": "https://...",
  "provider_used": "firecrawl",
  "request_id": "req_8f3a2c",
  "latency_ms": 1820,
  "cached": false
}
```
### 4.6 DLP Middleware
Two enforcement points:

**Outbound DLP** (query/URL before dispatch):
- Pattern matching (regex) on query text or URL
- Actions: `block`, `redact`, `reroute` (force self-hosted provider)
- Can whitelist allowed providers per policy (e.g. health queries → SearXNG only)
- Evaluated before provider dispatch, after policy engine

**Inbound DLP** (content returned from provider):
- Pattern matching on response content
- Actions: `redact_with` (replace match with placeholder), `block_response`
- Default patterns: API keys (`sk-...`), SSNs, email addresses (configurable off)
### 4.7 Audit Logger
Structured JSON, one line per request, append-only rotating file:

```json
{
  "ts": "2026-06-16T09:34:00Z",
  "request_id": "req_8f3a2c",
  "api_key_id": "key_agent1",
  "type": "scrape",
  "url": "https://wsj.com/...",
  "policy_matched": "paywalled_news",
  "provider_used": "playwright",
  "proxy_used": "residential_us",
  "judge_invoked": false,
  "judge_reasoning_tag": null,
  "latency_ms": 1820,
  "status": "success",
  "dlp_policy": "no_pii_upstream",
  "dlp_action": "pass",
  "attempt_number": 1
}
```

Implemented via Python `logging` with `RotatingFileHandler`. No external logging infrastructure required in v1. Format chosen for trivial SIEM ingestion later.

***
## 5. API Surface
### 5.1 REST API
```
POST /search
  Authorization: Bearer <key>
  Body: {
    "query": string,
    "num_results": integer?,          // default 10
    "provider": string?,              // override policy
    "policy_override": object?        // inline rule override
  }
  Returns: SearchResponse

POST /scrape
  Authorization: Bearer <key>
  Body: {
    "url": string,
    "format": "markdown"|"html"|"json",  // default markdown
    "provider": string?,
    "policy_override": object?,
    "wait_for_selector": string?,     // passed to browser providers
    "session_profile": string?        // named Playwright cookie jar
  }
  Returns: ScrapeResponse

GET  /health
  Returns: { status, providers: [{name, healthy, last_check_ts}] }

GET  /providers
  Returns: [ProviderMetadata]         // see section 7

POST /admin/reload
  Authorization: Bearer <admin_key>
  Returns: { reloaded: true, config_hash: string }
```

**Dry-run mode:** append `?dry_run=true` to `/search` or `/scrape` — returns what the policy engine would decide without executing. Used for debugging policy configs.
### 5.2 MCP Server
Exposes identical functionality as MCP tools. Auth via Bearer token in transport headers. Tool definitions:

```json
{
  "tools": [
    {
      "name": "web_search",
      "description": "Search the web. Provider selected automatically by policy.",
      "inputSchema": {
        "query": "string",
        "num_results": "integer?",
        "provider_hint": "string?"
      }
    },
    {
      "name": "web_scrape",
      "description": "Extract content from a URL. Provider selected automatically by policy.",
      "inputSchema": {
        "url": "string",
        "format": "markdown|html|json",
        "provider_hint": "string?",
        "session_profile": "string?"
      }
    }
  ]
}
```

REST and MCP share the same policy engine, provider adapters, DLP middleware, and audit logger. No duplication of logic.
### 5.3 Upstream MCP Client
For providers that expose an MCP server (e.g. Firecrawl MCP), the gateway speaks MCP upstream via an MCP client adapter. The provider adapter interface is identical — the transport is the only difference. Adding a new MCP-native provider requires only writing one adapter class.

***
## 6. Search Providers
| Provider | Type | Self-hosted | Notes |
|---|---|---|---|
| SearXNG | Meta-search | ✅ | Default self-hosted search |
| DuckDuckGo | Scrape-based | ✅ (no key) | Rate-limited, no API key needed |
| Brave Search API | Dedicated | ❌ | Fast, agent-optimized |
| Tavily | Agentic/RAG | ❌ | Built for agent workflows |
| Exa | Neural/semantic | ❌ | Best for similarity search |
| Perplexity API | AI-native | ❌ | Returns summarized answers |
| Linkup | Agentic | ❌ | Real-time focused |
| SerpAPI / ValueSERP | Google proxy | ❌ | When Google results required |
| Grok Search | AI-native | ❌ | |
| Gemini Search | AI-native | ❌ | |

Provider config includes: `api_key`, `base_url` (for self-hosted), `rate_limit`, `timeout`, `enabled`.

***
## 7. Scrape Providers
| Provider | Strength | Self-hosted |
|---|---|---|
| Jina Reader | Cheapest, simple pages, generous free tier | ✅ |
| Firecrawl | JS-heavy, structured extraction, anti-bot | ✅ (Docker) |
| Crawl4AI | Fully local, async, no external deps | ✅ |
| Zyte API | Hardest targets, AI extraction | ❌ (managed) |
| ScrapingBee | Proxy pool included | ❌ |
| ScrapingAnt | Proxy pool included | ❌ |
| Browserbase | Managed cloud browser | ❌ |

Recommended default priority chain (configurable): `jina → firecrawl → playwright_remote → zyte`

***
## 8. Browser Services (Remote, Never Embedded)
Browser services run as **separate Docker Compose services**, never inside the gateway image. The gateway calls them over the internal Docker network via HTTP/WebSocket. This provides:

- Independent scaling (run N browser replicas without touching the gateway)
- Independent updates (bump Chromium version without redeploying gateway)
- Crash isolation (browser OOM does not affect gateway)
- Swap implementations freely (replace Playwright with Camoufox by changing one compose service)

| Service | Protocol | Default Port | Use Case |
|---|---|---|---|
| `browserless/chrome` | REST + CDP/WS | 3000 | General headless Chrome |
| Playwright server (thin wrapper) | REST | 3000 | Auth sessions, cookie jars |
| Camoufox | REST wrapper | configurable | Anti-fingerprinting |
| FlareSolverr | REST | 8191 | Cloudflare challenge solving |
| Splash | REST (Lua scriptable) | 8050 | Lightweight JS rendering |

Gateway adapter example — no `import playwright` anywhere in the gateway:

```python
class PlaywrightRemoteAdapter(ProviderAdapter):
    async def scrape(self, url: str, options: ScrapeOptions) -> ScrapeResult:
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{self.base_url}/scrape", json={
                "url": url,
                "proxy": options.proxy_url,
                "wait_for": options.wait_for_selector,
                "cookies": options.session_cookies
            })
        return ScrapeResult.from_dict(resp.json())
```

Browser services are declared under a `browsers` Docker Compose profile — not started by default, activated with `--profile browsers`.

***
## 9. Proxy Integration
### 9.1 Named Proxies
Proxies are named in `config.yaml` and referenced by policy rules:

```yaml
proxies:
  gluetun:
    type: http
    url: http://gluetun:8888
  residential_us:
    type: http
    url: http://brightdata-proxy:24000
  tor_socks5:
    type: socks5
    url: socks5://tor:9050
```
### 9.2 Gluetun Integration
Gluetun runs as a sidecar service exposing an HTTP proxy on port 8888. The gateway container does **not** use `network_mode: service:gluetun` — that would route all gateway traffic through the VPN. Instead, `proxy_url` is injected per-request into httpx or the browser service call options based on policy match only.

```yaml
# docker-compose.yml (relevant excerpt)
services:
  gluetun:
    image: qmcgaw/gluetun
    cap_add: [NET_ADMIN]
    devices: [/dev/net/tun:/dev/net/tun]
    ports: ["8888:8888"]
    environment:
      - HTTPPROXY=on
      - VPN_SERVICE_PROVIDER=mullvad
      - VPN_TYPE=wireguard

  serpllm:
    build: .
    ports: ["8080:8080"]
    environment:
      - GLUETUN_PROXY=http://gluetun:8888
    depends_on: [gluetun]
    volumes:
      - ./config.yaml:/app/config.yaml
      - ./logs:/app/logs
      - ./sessions:/app/sessions
```

***
## 10. LLM Judge
### 10.1 Purpose
Makes structured routing decisions when the Tier 1 policy engine has no matching rule, or when a provider error triggers a retry decision. Never fires on requests with a clear policy match.
### 10.2 Input / Output
Input: minimal context packet (URL/query, task type, prior attempts with errors, available providers and proxies).
Output: strict JSON only — `{ provider, proxy, reasoning_tag, confidence, fallback_if_fail }`. `reasoning_tag` is written to audit log only, never acted on.
### 10.3 Recommended Models
| Model | Deployment | Notes |
|---|---|---|
| `gemma3:1b` via Ollama | ✅ Self-hosted | Zero cost, ~50ms, good JSON |
| `qwen2.5:3b` via Ollama | ✅ Self-hosted | Slightly smarter |
| `gpt-4o-mini` | ❌ Managed | Reliable structured output |
| `claude-haiku-3.5` | ❌ Managed | Best JSON compliance managed |

Default: local Ollama. The judge is a classification problem, not a reasoning problem — tiny models handle it well and avoid adding a remote API call to every ambiguous request.
### 10.4 Config
```yaml
llm_judge:
  enabled: true
  model: ollama/gemma3:1b
  ollama_url: http://ollama:11434
  triggers:
    - on_policy_miss
    - on_retry
    - on_error_class: [403, 429, "bot_detected", "timeout"]
  cache_decisions: true
  cache_ttl_seconds: 3600
  confidence_threshold: 0.70
```

***
## 11. Full Config Schema
```yaml
defaults:
  search_provider: searxng
  scrape_provider: jina
  timeout: 15
  retry:
    strategy: fallback           # fallback | exponential | none
    max_attempts: 3
    fallback_chain: [jina, firecrawl, playwright]

policies:
  - name: reddit
    match:
      domain: "*.reddit.com"
    scrape_provider: crawl4ai
    proxy: gluetun

  - name: paywalled_news
    match:
      domain_glob: ["*.wsj.com", "*.nytimes.com", "*.ft.com"]
    scrape_provider: playwright
    playwright_profile: wsj_session
    proxy: residential_us

  - name: cloudflare_protected
    match:
      on_error_class: ["bot_detected", 403]
    scrape_provider: flaresolverr

  - name: health_queries_local_only
    match:
      query_contains: ["diagnosis", "medication", "patient record"]
    allowed_providers: [searxng, crawl4ai]
    dlp_policy: no_cloud_health

proxies:
  gluetun:
    type: http
    url: http://gluetun:8888
  residential_us:
    type: http
    url: http://brightdata-proxy:24000

providers:
  searxng:
    base_url: http://searxng:8080
  jina:
    api_key: ${JINA_API_KEY}
  firecrawl:
    base_url: http://firecrawl:3002
    api_key: ${FIRECRAWL_API_KEY}
  brave:
    api_key: ${BRAVE_API_KEY}
  tavily:
    api_key: ${TAVILY_API_KEY}
  playwright:
    base_url: http://playwright:3000
  flaresolverr:
    base_url: http://flaresolverr:8191
  zyte:
    api_key: ${ZYTE_API_KEY}

llm_judge:
  enabled: true
  model: ollama/gemma3:1b
  ollama_url: http://ollama:11434
  triggers: [on_policy_miss, on_retry]
  cache_decisions: true
  cache_ttl_seconds: 3600
  confidence_threshold: 0.70

dlp_policies:
  - name: no_pii_upstream
    applies_to_providers: [tavily, brave, perplexity]
    outbound_rules:
      - pattern: "\\b\\d{3}-\\d{2}-\\d{4}\\b"
        action: block
      - pattern: "\\b[\\w.]+@[\\w.]+\\.[a-z]{2,}\\b"
        action: redact
    inbound_rules:
      - pattern: "sk-[a-zA-Z0-9]{32,}"
        action: redact_with: "[REDACTED_API_KEY]"

auth:
  keys:
    - id: key_agent1
      secret: ${AGENT1_KEY}
      label: "Hermes agent"
    - id: key_admin
      secret: ${ADMIN_KEY}
      label: "Admin"
      admin: true

logging:
  path: /app/logs/gateway.jsonl
  max_bytes: 10485760
  backup_count: 5

sessions:
  store_path: /app/sessions
  encryption_key: ${SESSION_ENCRYPTION_KEY}
```

***
## 12. Provider Metadata
Each adapter carries static metadata exposed via `GET /providers` and used by the DLP engine:

```python
@dataclass
class ProviderMetadata:
    name: str
    self_hosted: bool
    data_retention_days: int | None    # None = unknown/undisclosed
    trains_on_queries: bool | None
    gdpr_compliant: bool
    hipaa_compliant: bool
    data_residency: list[str]          # ["US"] | ["EU"] | ["local"]
    privacy_policy_url: str | None
    mcp_native: bool                   # speaks MCP upstream
    capabilities: list[str]            # ["search", "scrape", "crawl", "extract"]
```

The DLP engine can use `self_hosted: true` as a routing constraint in policy rules — e.g. force all queries matching a pattern to only route to providers where `self_hosted=true`.

When a UI is built, this metadata drives a provider information panel showing data policies to operators.

***
## 13. Authentication
- Bearer token checked in FastAPI middleware on every request
- Tokens are opaque strings stored in config (`.env` or `config.yaml`)
- `api_key_id` (not the secret) is written to audit log
- Admin key required for `/admin/*` endpoints
- v1: flat key list in config, no expiry, no rotation UI
- RBAC deferred to LiteLLM layer above — serpLLM keys are service identities, not user identities
- `request_id` passed as response header and written to audit log; LiteLLM should forward it as a header for cross-layer audit trail joins

***
## 14. Build Order
Recommended implementation sequence:

1. FastAPI skeleton — health endpoint, config loader, Bearer auth middleware
2. Normalized response schemas (`SearchResponse`, `ScrapeResponse`)
3. Policy engine — YAML parser, Tier 1 rule matcher, domain glob support
4. Jina + SearXNG adapters (simplest, zero infra required)
5. Audit logger (structured JSON rotating file)
6. Firecrawl + Crawl4AI adapters
7. Proxy injection layer (per-request httpx proxy config)
8. Docker Compose with Gluetun integration
9. FlareSolverr + Splash + Playwright remote adapters
10. LLM judge (Ollama sidecar, policy miss trigger only first)
11. Retry/fallback chain logic in router layer
12. DLP middleware (outbound first, inbound second)
13. MCP server (downstream — agents connect via MCP)
14. Upstream MCP client (for MCP-native providers like Firecrawl)
15. Provider health check background task
16. Session/cookie jar store (encrypted, named profiles)
17. `dry_run` mode
18. Remaining provider adapters (Brave, Tavily, Exa, Zyte, etc.)

***
## 15. Explicitly Out of Scope (v1)
- No UI of any kind
- No enterprise RBAC (deferred to LiteLLM above)
- No multi-tenancy
- No distributed/clustered deployment
- No built-in caching layer beyond LLM judge decision cache (can be added via Redis later)
- No fine-tuning pipeline for judge decisions (though audit logs are structured to enable this later)
- No provider billing/cost tracking UI

***
## 16. LiteLLM Integration Boundary
```
User / Agent
     ↓
  LiteLLM                ← model routing, user RBAC, spend limits, team keys
     ↓
  Tool calls: web_search / web_scrape
     ↓
  serpLLM             ← provider routing, proxy, DLP, audit
     ↓
  Search & Scrape Providers
```

serpLLM is a **service identity** in LiteLLM's model — one API key represents the gateway. All user/team/org identity lives in LiteLLM. Cross-layer audit is achieved by passing `X-Request-ID` as a header through the stack and joining on it.
