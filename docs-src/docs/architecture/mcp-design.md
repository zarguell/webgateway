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

- **Transport:** Streamable HTTP (stateless)
- **Library:** `mcp>=1.27,<2` — official Python MCP SDK via `FastMCP`
- **Auth:** Bearer token, shared with REST API via `McpAuthMiddleware`
- **Dispatch:** Tools call `GatewayService.search()` / `.extract()` — the same pipeline as the REST API
- **Returns:** JSON strings (`json_response=True`)

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
