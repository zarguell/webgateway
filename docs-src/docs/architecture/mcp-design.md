# MCP Design

## Two-tool contract

serpLLM's MCP server exposes exactly two tools:

- **`web_search`** — Search the web via configured providers
- **`web_extract`** — Extract content from a URL

This is intentional and stable.

## Why not combo tools (search_and_fetch, fetch_many, etc.)

Community MCP servers that wrap SearXNG commonly add tools like `search_and_fetch`, `fetch_many`, or `research` that combine search with automatic extraction. These make sense as standalone servers — they need to be useful out of the box.

serpLLM is **infrastructure for intelligent agents**, not a standalone tool. Agents already handle composition:

- The agent calls `web_search`, inspects results, and decides *which* URLs are worth extracting. Auto-extracting every result wastes tokens on irrelevant pages.
- The agent can fan out multiple `web_extract` calls concurrently. Parallelism is the agent's job, not the gateway's.
- Keeping tools atomic means the agent stays in control of the orchestration logic.

## Why two tools is the right surface area

| Concern | Combo tools | Two atomic tools |
|---|---|---|
| Token efficiency | Wastes on irrelevant auto-extracts | Agent triages first |
| Test surface | Every combo needs tests | Each tool is independently testable |
| Breaking changes | Changing combo behavior breaks agent flows | Atomic changes don't cascade |
| Documentation | More tools = more docs to maintain | Two tools, stable contract |

## Implementation

- **Transport:** Streamable HTTP (stateless) — MCP spec 2025-03-26
- **Library:** `mcp>=1.27,<2` — official Python MCP SDK via `FastMCP`
- **Auth:** Bearer token, shared with REST API via `McpAuthMiddleware`
- **Dispatch:** Tools call `GatewayService.search()` / `.extract()` — the same pipeline as the REST API
- **Returns:** JSON strings (`json_response=True`)

### Streamable HTTP and reverse proxies

serpLLM's MCP endpoint uses Streamable HTTP (POST-only JSON-RPC). Some MCP clients
probe with a GET request first, expecting SSE. The server returns 200 with a JSON-RPC
response on GET to satisfy the probe, then all actual MCP communication flows over POST.

When serpLLM is deployed behind a reverse proxy (Traefik, nginx, Caddy):

1. **Host header validation.** FastMCP's Streamable HTTP validates the Host header.
   If using Traefik, add a `fix-host` middleware that sets `Host` to the server's
   listening address (e.g. `localhost:8080`):

   ```yaml
   http:
     middlewares:
       fix-host:
         headers:
           customRequestHeaders:
             Host: "localhost:8080"
     routers:
       serpllm:
         rule: Host(`your.domain.com`)
         service: serpllm
         middlewares:
           - fix-host
   ```

2. **uvicorn HTTP parser.** The entrypoint uses `h11` (pure-Python) by default to
   avoid strict Host validation at the parser level. For deployments that need
   `httptools` (for performance), set `FORWARDED_ALLOW_IPS` to your proxy's subnet:

   ```yaml
   environment:
     FORWARDED_ALLOW_IPS: "172.18.0.0/16"
   ```

3. **GET probe response.** Some MCP clients send a GET before POST. The server
   returns `{"jsonrpc":"2.0","id":null,"result":{"serverInfo":"serpLLM"}}` on
   GET — a lightweight JSON-RPC response that satisfies both SSE-probing clients
   (OpenCode) and JSON-expecting clients (Hermes).

### Extraction modes

The gateway has three extraction modes, selected automatically:

| Mode | Trigger | Pipeline | Use case |
|---|---|---|---|
| **Strategy** | Policy rule with `extract_strategy` | Custom HTML parser (e.g. Reddit listing) | Sites with dedicated extractors |
| **Readability** | Default `format: "markdown"` | readability + trafilatura → markdownify | Articles, blogs, docs |
| **Text** | `format: "text"` (no policy match) | `document.body.innerText` via IPW | JS-heavy listing pages |

**Text mode** is automatically routed to InvisiblePlaywright, which extracts
`document.body.innerText` — the browser's built-in "select all → copy as plain
text." This reduces 600KB+ JS-rendered pages to 1-15KB of clean visible text.

When a policy rule matches a URL, `format: "text"` is upgraded to `"markdown"` so
the policy's strategy or extraction pipeline runs normally. The agent doesn't need
to know which mode to use for which site.

## Extraction strategies

Policy rules can configure per-domain extraction strategies that enrich the extract response with structured data:

```yaml
policies:
  - name: imdb
    match:
      domain_glob: "*.imdb.com"
    extract_strategy:
      priority:
        - json_ld
        - meta_extract
        - article_extract
```

Strategies are tried in priority order. The first to produce data wins. Strategies **supplement** the content pipeline — they never replace it. The agent always receives:

| Field | Always present? | Description |
|---|---|---|
| `content` | ✅ | Full page content extracted by trafilatura as markdown |
| `format` | ✅ | `"markdown"` by default, `"json"` if requested |
| `structured_data` | ✅ | JSON object (or null) from the matched strategy — e.g. `@type: Movie`, `aggregateRating`, `genre` |

This means an agent calling `web_extract("https://www.imdb.com/title/tt0111161/")` gets both the full page text AND structured metadata like ratings, runtime, and genre — without any extra parameters.

Available strategies:

| Strategy | What it extracts |
|---|---|
| `json_ld` | `<script type="application/ld+json">` blocks, scored by `@type` priority |
| `meta_extract` | Open Graph, Twitter Card, and standard `<meta>` tags |
| `article_extract` | Default trafilatura → markdownify pipeline (always the fallback) |

See `src/serp_llm/post_processing/strategies/` for implementations and `src/serp_llm/post_processing/pipeline.py` for the integration.

See `src/serp_llm/mcp/server.py` for the implementation.

## Bot detection auto-routing

When a provider returns a CAPTCHA or bot-detection page (Cloudflare, DataDome, etc.), the gateway automatically reroutes through a bot-solving provider like FlareSolverr or invisible_playwright — no per-domain policy rule required.

Detection is pattern-based (see `BOT_BLOCK_PATTERNS` in `src/serp_llm/service.py`). This is best-effort and **will need tuning** based on real-world telemetry. False negatives (blocked pages that slip through) are preferred over false positives (legitimate content misidentified as a bot block).

Future improvements:
- Per-provider success/failure telemetry to tune detection patterns
- Adaptive fallback (skip a provider permanently if it consistently returns bot pages for a domain)
- Shared community block-pattern database
