# Local Development: Using serpLLM for Your Own Research

## Overview

serpLLM can run locally with your system Chrome browser as the extraction engine and SearXNG as the search backend. No cloud API keys needed. Just Docker, Docker Compose, and Chrome.

The local stack is a lightweight 3-service setup:

- **serpLLM** at `localhost:8080` — the core gateway
- **SearXNG** at `localhost:8081` — self-hosted metasearch engine
- **CDP Chrome sidecar** at `localhost:9222` — bridges Docker to your host Chrome via Chrome DevTools Protocol

Your AI agent connects to the gateway through MCP, giving it `web_search` and `web_extract` tools that route through your own infrastructure.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                      Your Machine                        │
│                                                          │
│  ┌──────────┐     MCP     ┌──────────────┐              │
│  │ AI Agent │◄───────────►│  serpLLM  │              │
│  │ (OpenCode│             │  localhost:8080             │
│  │ , Claude │             │              │              │
│  │ , etc.)  │             └──────┬───────┘              │
│  └──────────┘                    │                      │
│                                  │                      │
│                    ┌─────────────┼─────────────┐        │
│                    │             │             │        │
│                    ▼             ▼             ▼        │
│  ┌─────────────────┐  ┌──────────┐  ┌────────────────┐  │
│  │  CDP Chrome      │  │ SearXNG  │  │  Cloud/Other   │  │
│  │  Sidecar          │  │ :8081   │  │  Providers     │  │
│  │  localhost:9222   │  │         │  │  (optional)    │  │
│  └────────┬─────────┘  └──────────┘  └────────────────┘  │
│           │                                               │
│           ▼                                               │
│  ┌──────────────────┐                                     │
│  │  Host Chrome     │                                     │
│  │  (your browser)  │                                     │
│  └──────────────────┘                                     │
└─────────────────────────────────────────────────────────┘
```

**Data flow for an extract request:**

1. AI agent calls `web_extract` via MCP
2. serpLLM matches a policy rule for the target URL
3. The gateway dispatches to the `cdp_chrome` provider
4. The CDP Chrome sidecar forwards the request to the host Chrome's debugging endpoint at `localhost:9222`
5. Chrome loads the page and returns the rendered HTML
6. The sidecar converts HTML to markdown and sends it back through the gateway
7. The gateway applies DLP, caching, and post-processing before returning the result

For search requests, the flow is simpler: serpLLM routes to SearXNG, which aggregates results from multiple public search engines.

---

## Prerequisites

- **Docker** and **Docker Compose** — for running the gateway and SearXNG
- **Google Chrome** — installed on your host machine (macOS, Linux, Windows)
- **No cloud API keys needed** — uses your local Chrome and SearXNG

---

## Quick Start

```bash
# 1. Launch Chrome with CDP debugging
./scripts/launch-chrome-cdp.sh

# 2. Start the stack
docker compose -f docker-compose.local.yml --profile local up -d --build

# 3. Test extraction
curl -X POST http://localhost:8080/extract \
  -H "Authorization: Bearer local-agent-key" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com"}'

# 4. Test search
curl -X POST http://localhost:8080/search \
  -H "Authorization: Bearer local-agent-key" \
  -H "Content-Type: application/json" \
  -d '{"query": "fastapi python web framework"}'
```

---

## Chrome CDP Setup

The `scripts/launch-chrome-cdp.sh` script starts Chrome with remote debugging enabled:

```bash
# Launch Chrome with CDP debugging on port 9222
# Uses an isolated profile to avoid conflicts with your default profile

/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/chrome-cdp-profile \
  --no-first-run \
  --no-default-browser-check
```

**Why a separate profile?** Chrome blocks automation of the default browser profile for security reasons. An isolated profile at `/tmp/chrome-cdp-profile` avoids this restriction entirely. You can navigate normally in this Chrome window, and it won't affect your default profile.

**Verifying it's working:**

```bash
curl http://localhost:9222/json/version
```

You should see a JSON response with browser version, web socket URL, and other debugging info. If you get `Connection refused`, Chrome is not running with CDP enabled.

---

## Docker Compose Breakdown

The stack is defined in `docker-compose.local.yml`:

| Service | Role | Port | Image |
|---|---|---|---|
| serpllm | Core gateway | 8080 | Built from `.` |
| searxng | Search engine | 8081 | `searxng/searxng` |
| cdp-chrome | Chrome CDP bridge | 9222 | Built from `services/cdp-chrome/` |

- **serpllm** — The main FastAPI application. Built from the current directory. Routes search requests to SearXNG, extract requests to the CDP Chrome sidecar.
- **searxng** — A self-hosted metasearch engine that aggregates results from Google, Bing, DuckDuckGo, and other public search engines. No API keys required.
- **cdp-chrome** — A lightweight Python service that acts as a bridge between serpLLM and your host Chrome. It takes a URL from the gateway, opens it in Chrome via CDP, extracts the rendered content, converts it to markdown, and returns it. The Dockerfile lives in `services/cdp-chrome/`.

---

## Configuration

The local configuration lives in `config.local.yaml`. Key settings:

```yaml
defaults:
  search_provider: searxng
  extract_provider: cdp_chrome

policies:
  - name: wikipedia
    match:
      domain: "*.wikipedia.org"
    extract_provider: cdp_chrome

mcp:
  enabled: true
```

**What this config does:**

- **Default search** routes to SearXNG at `localhost:8081`
- **Default extraction** routes to the CDP Chrome sidecar at `localhost:9222`
- **Wikipedia policy** uses CDP Chrome for JS-heavy encyclopedia articles
- **MCP is enabled** so AI agents can connect via the MCP protocol
- **Post-processing is bypassed** for `cdp_chrome` — the sidecar returns clean markdown directly, so the gateway skips boilerplate removal and markdown conversion

---

## Wiring MCP

serpLLM exposes an MCP endpoint at `http://localhost:8080/mcp`. Configure your AI agent to connect to it:

### OpenCode (`opencode.json`)

```yaml
mcp:
  servers:
    serpllm:
      url: http://localhost:8080/mcp
```

### Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "serpllm": {
      "url": "http://localhost:8080/mcp"
    }
  }
}
```

### Any MCP-compatible client

```json
{
  "mcpServers": {
    "serpllm": {
      "url": "http://localhost:8080/mcp"
    }
  }
}
```

Once connected, your agent gains two tools:

- **`web_search`** — searches the web through SearXNG
- **`web_extract`** — extracts rendered page content through your system Chrome

No API key configuration needed inside the agent. All routing happens at the gateway layer.

---

## Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| "Chrome not connected" (503) | Chrome not running with CDP | Run `./scripts/launch-chrome-cdp.sh` |
| Connection refused on port 9222 | Sidecar can't reach host Chrome | Ensure `host.docker.internal` resolves. macOS: Docker Desktop handles this. Linux: add `--add-host=host.docker.internal:host-gateway` to Docker Compose |
| Empty content from extract | Page blocked by bot detection | CDP Chrome has no stealth features. Try `invisible_playwright` for protected pages |
| Port 8080 already in use | Another serpllm instance running | Stop it with `docker compose -f docker-compose.local.yml down` or change the port mapping |
| Search returns no results | SearXNG still starting up | SearXNG takes 10-30 seconds on first boot. Wait and retry |
| Chrome window visible but no response | Chrome is on a different display (Linux/Wayland) | Set `DISPLAY=:0` or use Xvfb in the launch script |

---

## Tips

**Policy routing for specific domains.** Add rules in `config.local.yaml` to route certain sites through different providers:

```yaml
policies:
  - name: google-docs
    match:
      domain: "docs.google.com"
    extract_provider: cdp_chrome
    extract_options:
      wait_for: ".docs-texteventtarget-iframe"
      timeout: 30
```

**Use the admin UI.** Open `http://localhost:8080/admin` to monitor provider health, inspect cache state, view recent requests, and manage API keys. The admin UI is available without extra configuration.

**Monitor with logs.** Tail the gateway logs to see what's happening:

```bash
docker compose -f docker-compose.local.yml logs -f serpllm
```

**Cache frequently accessed pages.** Set TTL rules in your policy to cache common sources:

```yaml
cache:
  policies:
    - match:
        domain: "*.python.org"
      ttl: 86400
      validate: true
```

This caches Python docs for a full day, reducing load on SearXNG and Chrome for repeated lookups.
