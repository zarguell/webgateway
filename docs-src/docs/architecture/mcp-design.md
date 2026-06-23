# MCP Design

## Two-tool contract

WebGateway's MCP server exposes exactly two tools:

- **`web_search`** — Search the web via configured providers
- **`web_extract`** — Extract content from a URL

This is intentional and stable.

## Why not combo tools (search_and_fetch, fetch_many, etc.)

Community MCP servers that wrap SearXNG commonly add tools like `search_and_fetch`, `fetch_many`, or `research` that combine search with automatic extraction. These make sense as standalone servers — they need to be useful out of the box.

WebGateway is **infrastructure for intelligent agents**, not a standalone tool. Agents already handle composition:

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

See `src/webgateway/mcp/server.py` for the implementation.
