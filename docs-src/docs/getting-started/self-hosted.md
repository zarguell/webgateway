# Self-Hosted All-in-One Deployment

## Overview

This deployment gives you the full serpLLM stack - web search, content extraction, and library documentation - with zero API keys. One `docker compose up` and you're running.

Six containers work together:

| Container | Role | Externally Accessible |
|---|---|---|
| Traefik | Reverse proxy + TLS termination | Yes (ports 80, 443) |
| serpLLM | API gateway + policy engine | Via Traefik only |
| SearXNG | Meta-search aggregator | Internal |
| Crawl4AI | Full browser rendering | Internal |
| DevDocs | 100+ library documentation sets | Internal |
| InvisiblePlaywright | Stealth anti-bot browser | Internal |

Only serpLLM is exposed externally (through Traefik). All backend services stay on the internal Docker network. **8 GB RAM recommended.**

## Prerequisites

- Docker and Docker Compose v2
- 8 GB RAM minimum
- OpenSSL (for self-signed certificate generation)
- `make` and `curl` (for cert setup)

## Quick Start

Five commands to get running:

```bash
# 1. Clone the repo
git clone https://github.com/zarguell/serp_llm.git && cd serp_llm

# 2. Generate a self-signed TLS certificate
mkdir -p certs dynamic
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout certs/local.key -out certs/local.crt \
  -subj "/CN=gateway"
cat > dynamic/tls.yml << 'EOF'
tls:
  certificates:
    - certFile: /certs/local.crt
      keyFile: /certs/local.key
EOF

# 3. Create minimal SearXNG settings
curl -sL https://raw.githubusercontent.com/searxng/searxng/master/searxng/settings.yml > searxng-settings.yml

# 4. Start everything
docker compose -f docker-compose.selfhosted.yml up -d

# 5. Wait for health checks to pass
docker compose -f docker-compose.selfhosted.yml ps
```

After the `ps` command, all services should show `Up` or `healthy`. The first startup takes 30-60 seconds while Crawl4AI and InvisiblePlaywright initialize.

## Verify It Works

Test search:

```bash
curl -k -X POST https://gateway.localhost/search \
  -H "Authorization: Bearer change-me-in-production" \
  -H "Content-Type: application/json" \
  -d '{"query": "python fastapi tutorial"}'
```

Test content extraction:

```bash
curl -k -X POST https://gateway.localhost/extract \
  -H "Authorization: Bearer change-me-in-production" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com"}'
```

The `-k` flag skips TLS verification for the self-signed cert. In production with Let's Encrypt, remove it.

## Architecture

```
                          Internet (HTTPS :443)
                                    |
                                 Traefik
                              (TLS termination)
                                    |
                              serpLLM
                              (policy engine)
                          /       |        |        \
                         ↓        ↓        ↓         ↓
                    SearXNG   Crawl4AI  DevDocs   Invisible
                    :8080     :11235    :9292    Playwright
                                                   :3001
                              │
                              └─ Jina Reader (remote, free tier)
```

Traefik terminates TLS on ports 443 (HTTPS) and redirects port 80 to 443. It routes requests to serpLLM based on the `Host(gateway.localhost)` rule. serpLLM dispatches to internal backend services based on policy configuration. None of the backend containers are exposed outside the Docker network.

## Provider Reference

| Provider | Role | Self-Hosted | API Key | Notes |
|---|---|---|---|---|
| SearXNG | Search | Yes | No | Meta-search aggregator, queries 70+ engines |
| Jina Reader | Extract (simple) | No | Free tier | Fast markdown extraction, 20 RPM free |
| Crawl4AI | Extract (JS-heavy) | Yes | No | Full browser rendering, supports JS |
| Crawl4AI MD | Extract (lightweight) | Yes | No | Markdown-only mode, same container |
| DevDocs | Search (library docs) | Yes | No | 100+ official docs for popular frameworks |
| InvisiblePlaywright | Extract (stealth) | Yes | No | Anti-bot fingerprint browser, Firefox |

Search defaults to SearXNG. Extract defaults to Jina (free tier), with a fallback chain: Jina → Crawl4AI MD → Crawl4AI. No API keys required for any self-hosted provider.

## Configuration

All settings live in `config.selfhosted.yaml`, mounted into the serpLLM container. Here's what the key sections do:

```yaml
defaults:
  search_provider: searxng
  extract_provider: jina
  timeout: 15
  retry:
    strategy: fallback
    max_attempts: 3
    fallback_chain:
      - jina
      - crawl4ai_md
      - crawl4ai

providers:
  searxng:
    base_url: http://searxng:8080
  jina:
    base_url: https://r.jina.ai
  crawl4ai:
    base_url: http://crawl4ai:11235
  crawl4ai_md:
    base_url: http://crawl4ai:11235
  invisible_playwright:
    base_url: http://invisible-playwright:3001

auth:
  keys:
    - id: key_agent1
      secret: change-me-in-production
```

- **`defaults`** - Sets the primary search/extract providers, request timeout, and the fallback chain if a provider fails.
- **`providers`** - Each provider's internal URL. Service names match the Docker Compose container names.
- **`auth`** - API keys for clients. Change `change-me-in-production` before exposing to a network.
- **`cache`** - SQLite-backed response cache. Default TTL is 300 seconds.
- **`post_processing`** - Content pipeline: Trafilatura extraction, markdown conversion, boilerplate cleaning.
- **`circuit_breaker`** - Trips after 5 errors in 60 seconds, cooldown for 120 seconds.

**To edit:** change `config.selfhosted.yaml`, then either:

```bash
docker compose restart serpllm
```

Or trigger hot reload (no restart):

```bash
curl -k -X POST https://gateway.localhost/admin/reload \
  -H "Authorization: Bearer change-me-in-production"
```

**Policy routing example** - route specific domains to specialized providers:

```yaml
policies:
  - name: docs
    match:
      domain_glob:
        - "*.wikipedia.org"
        - "*.docs.python.org"
        - "*.developer.mozilla.org"
    extract_provider: crawl4ai_md
```

This sends Wikipedia, Python docs, and MDN to the lightweight Crawl4AI markdown extractor instead of the default Jina flow.

## Upgrading to Production (Let's Encrypt)

Switch from self-signed certs to real TLS with three changes.

**Prerequisites:** A public domain with a DNS A record pointing to your server.

### 1. Change Traefik command arguments

Replace the self-signed file provider with Let's Encrypt ACME:

```yaml
# Replace this line in docker-compose.selfhosted.yml:
#   - "--providers.file.filename=/dynamic/tls.yml"

# With these ACME arguments:
command:
  - "--entrypoints.websecure.address=:443"
  - "--certificatesresolvers.le.acme.tlschallenge=true"
  - "--certificatesresolvers.le.acme.email=admin@yourdomain.com"
  - "--certificatesresolvers.le.acme.storage=/letsencrypt/acme.json"
  - "--providers.docker=true"
  - "--providers.docker.exposedbydefault=false"
  - "--providers.docker.network=serpllm-net"
```

### 2. Add Let's Encrypt storage volume

```yaml
volumes:
  - /var/run/docker.sock:/var/run/docker.sock:ro
  - ./certs:/certs:ro
  - ./letsencrypt:/letsencrypt           # Add this line
```

And add the volume at the bottom:

```yaml
volumes:
  crawl4ai-cache:
  letsencrypt:                            # Add this line
```

### 3. Update Docker labels

```yaml
# Change this:
- "traefik.http.routers.serpllm.tls=true"

# To this:
- "traefik.http.routers.serpllm.tls.certresolver=le"
```

Also update the `Host` rule to your real domain:

```yaml
- "traefik.http.routers.serpllm.rule=Host(`gateway.yourdomain.com`)"
```

Restart the stack:

```bash
docker compose -f docker-compose.selfhosted.yml down
docker compose -f docker-compose.selfhosted.yml up -d
```

Traefik automatically requests certificates on first connection and renews them 30 days before expiry.

## Troubleshooting

| Problem | Check This |
|---|---|---|
| Container won't start | `docker compose logs <service>` for error details |
| Certificate errors | Regenerate certs: `rm certs/* dynamic/tls.yml` then re-run the quick start cert step, restart traefik |
| Health checks failing | `docker compose ps` - check the STATUS column. First startup can take 60s |
| Auth errors | Verify `config.selfhosted.yaml` has the right auth keys, restart serpllm |
| SearXNG returning empty results | Check `searxng-settings.yml` has valid search engines configured |
| Crawl4AI crashes | Check memory limits. Needs at least 1 GB reserved, 4 GB limit recommended |
| Port 443 already in use | Stop any other service listening on 443, or change Traefik's port mapping |
| MCP client says "invalid content type" or "SSE error" | The MCP endpoint uses Streamable HTTP. Some clients probe with GET first — the server handles this. If POST requests return `421 Misdirected Request` in the logs, uvicorn is rejecting the proxied Host header. The entrypoint uses h11 by default to prevent this. If you've overridden the CMD, ensure `--http h11` is set, or set `FORWARDED_ALLOW_IPS` to your proxy's subnet |
| MCP POST returns "Invalid Host header" or 421 | FastMCP's Streamable HTTP validates the Host header. For Traefik, add a `fix-host` middleware that sets `Host: "localhost:8080"` (the server's listening address). See the Traefik config in `dynamic/tls.yml` |
| Reddit / protected sites blocked or CAPTCHA'd | Add a policy rule routing the domain to `invisible_playwright`. For persistent authenticated access, create a cookie session — see the [bot-protected sites guide](/sessions/bot-protected-sites/) |
| git pull overwrites local config changes | The server runs from a git checkout. `git pull` or `git reset --hard` reverts local config edits. Use `dynamic/tls.yml` for local overrides (it's gitignored) or keep a backup of custom config changes |
| Multi-tag push noise on GitHub | Only tag actual releases (e.g. `v0.5.0`). For dev deploys, use `git pull && docker compose build serpllm && docker compose up -d --force-recreate` — no tag or GHCR push needed |

## Extraction Modes

serpLLM has three extraction modes, selected automatically based on the URL and the `format` parameter:

| Mode | Trigger | What it does | Best for |
|---|---|---|---|
| **Strategy** | Policy rule with `extract_strategy` | Site-specific HTML parser (e.g., Reddit listing feeds) | Sites with custom extractors |
| **Readability** | `format: "markdown"` (default) | Article extraction via readability + trafilatura | News, blogs, docs, individual posts |
| **Text** | `format: "text"` (no policy match) | `document.body.innerText` via InvisiblePlaywright | JS-heavy listing pages (Zillow, Redfin) |

**How text mode works:** When an agent sends `format: "text"` and no policy rule matches the URL, the gateway automatically routes the request to InvisiblePlaywright and instructs it to extract `document.body.innerText` — the browser's built-in "select all → copy as plain text." This strips all HTML, scripts, styles, tracking, and navigation without any parsing, typically reducing 600KB+ of JS-rendered HTML to 1-15KB of clean visible text.

**Policy-protected URLs:** If a policy rule matches (e.g., Reddit with its listing strategy), `format: "text"` is ignored and the pipeline uses HTML extraction — the policy knows best for that site.

### Text mode limitations

`innerText` returns only rendered visible DOM text. It will miss or produce incomplete
output for:

| Pattern | Why | Better approach |
|---|---|---|
| `display: none` / hidden elements | `innerText` skips non-rendered nodes | `textContent` — returns all text regardless of visibility |
| `<canvas>` / SVG charts | Content is pixels or vectors, not DOM text | Screenshot + OCR, or intercept the data API |
| Click-to-reveal / infinite scroll | Content loads on user interaction | Pre-scroll with Playwright before extracting |
| Shadow DOM components | Standard traversal doesn't pierce shadow roots | `page.evaluate()` to reach into shadow DOM |
| `<iframe>` content | Separate document, not in parent DOM | `page.frame().innerText` targeting the specific frame |
| `<script>` JSON blobs (Next.js, etc.) | Data embedded for hydration, not displayed | `page.content()` + HTML parser to extract JSON |

For most listing pages (Zillow, Redfin, product catalogs), the visible text is exactly
what an agent needs — prices, descriptions, addresses, ratings. The failure patterns
above are edge cases that readability/trafilatura also cannot handle, so text mode
isn't losing capability the other modes had.

## Auth Keys

The config file uses environment variable references for secrets:

```yaml
auth:
  keys:
    - id: key_agent1
      secret: ${AGENT1_KEY}     # resolved from .env at startup
```

The `.env` file at the project root sets these values. Do NOT hardcode secrets in YAML — the config is checked into git.

Reset everything and start fresh:

```bash
docker compose -f docker-compose.selfhosted.yml down -v
```

## Resource Requirements

| Container | Base RAM | Active RAM |
|---|---|---|
| Traefik | ~20 MB | ~20 MB |
| serpLLM | ~200 MB | ~200 MB |
| SearXNG | ~100 MB | ~200 MB |
| Crawl4AI | ~270 MB | ~1-4 GB |
| DevDocs | ~200 MB | ~300 MB |
| InvisiblePlaywright | ~500 MB | ~2 GB |
| **Total** | **~1.3 GB** | **~4-8 GB** |

Crawl4AI and InvisiblePlaywright are the heavy containers. Their memory usage depends on page complexity. If you don't need stealth browsing, remove InvisiblePlaywright from the compose file and disable it in `config.selfhosted.yaml`.
