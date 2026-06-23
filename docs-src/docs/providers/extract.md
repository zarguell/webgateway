# Extract (Content Extraction) Providers

## Jina Reader

Lightweight read-it-later style extraction. Generous free tier. Returns clean markdown from any URL. Good default for most content.

## Firecrawl

Full-featured extraction with JavaScript rendering and structured output. Requires `FIRECRAWL_API_KEY`. Available as cloud API or self-hosted.

## Invisible Playwright

Stealth browser based on C++-patched Firefox 150. Undetectable extraction for hard targets. Runs as a separate Docker container with the `stealth` profile.

## Zyte

Cloud extraction service with managed proxy rotation and headless-browser rendering. Handles hard targets with automatic CAPTCHA solving. Requires a `ZYTE_API_KEY` (HTTP Basic Auth).

### Configuration

```yaml
providers:
  zyte:
    api_key: ${ZYTE_API_KEY}
    base_url: https://api.zyte.com
    timeout: 120
```

### Policy Routing

```yaml
policies:
  - name: hard_target_extract
    match:
      domain_glob: ["*.protected-site.com"]
    extract_provider: zyte
```

### API Calls

```bash
curl -X POST http://localhost:8080/extract \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "provider": "zyte"}'
```

## FlareSolverr

Self-hosted proxy that bypasses Cloudflare and DDoS-GUARD challenges via headless browser. No API key needed — runs as a Docker sidecar. Extract-only.

### Docker Setup

```yaml
services:
  flaresolverr:
    image: ghcr.io/flaresolverr/flaresolverr:latest
    ports:
      - "8191:8191"
    environment:
      - LOG_LEVEL=info
    restart: unless-stopped
```

### Configuration

```yaml
providers:
  flaresolverr:
    base_url: http://flaresolverr:8191
    timeout: 120
```

### Policy Routing

Route Cloudflare-protected sites to FlareSolverr:

```yaml
policies:
  - name: cloudflare_bypass
    match:
      domain_glob: ["*.cloudflare-protected.com"]
    extract_provider: flaresolverr
```

### API Calls

```bash
curl -X POST http://localhost:8080/extract \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "provider": "flaresolverr"}'
```

## Crawl4AI

Self-hosted extraction with full browser rendering (Playwright-based). Runs as a Docker sidecar and registers two separate providers that point at the same container. Extract-only — no search capability.

### Docker Setup

```yaml
services:
  crawl4ai:
    image: unclecode/crawl4ai:0.8.6
    ports:
      - "11235:11235"
    shm_size: 1gb
    environment:
      - CRAWL4AI_API_TOKEN=${CRAWL4AI_API_TOKEN:-}
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:11235/health"]
      interval: 30s
      retries: 3
```

Requires `shm_size: 1gb` and at least 4GB RAM limit. The API token is optional — set `CRAWL4AI_API_TOKEN` in the environment and pass it as a `Bearer` token to the Crawl4AI API if you need auth.

### Two Modes

| Provider | Endpoint | Use Case |
|----------|----------|----------|
| `crawl4ai` | `POST /crawl` | Full browser rendering, JavaScript execution |
| `crawl4ai_md` | `POST /md` | Lightweight markdown extraction, cheaper |

### Configuration

```yaml
providers:
  crawl4ai:
    base_url: http://crawl4ai:11235
    timeout: 30
    cost_units_per_call: 0.5
    specialization: browser
  crawl4ai_md:
    base_url: http://crawl4ai:11235
    timeout: 30
    cost_units_per_call: 0.3
    specialization: markdown
```

### Policy Routing

Use domain-based rules to pick the right mode for each target:

```yaml
policies:
  - name: simple_docs_extract
    match:
      domain_glob: ["*.wikipedia.org", "*.docs.python.org"]
    extract_provider: crawl4ai_md
  - name: js_heavy_extract
    match:
      domain_glob: ["*.react-app.com"]
    extract_provider: crawl4ai
```

### API Calls

```bash
# Full browser crawl with JS rendering
curl -X POST http://localhost:8080/extract \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "provider": "crawl4ai"}'

# Lightweight markdown extraction
curl -X POST http://localhost:8080/extract \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "provider": "crawl4ai_md"}'
```
