# Provider Setup Guides

## SearXNG (Self-Hosted)

Runs as a sidecar container. No API key needed. Configured in `config.yaml`:

```yaml
providers:
  searxng:
    base_url: http://searxng:8080
```

## Brave Search

1. Sign up at [Brave Search API](https://brave.com/search/api/)
2. Set `BRAVE_API_KEY` in `.env`
3. Add to config:

```yaml
providers:
  brave:
    base_url: https://api.search.brave.com
```

## Tavily

1. Sign up at [Tavily](https://tavily.com/)
2. Set `TAVILY_API_KEY` in `.env`

## Jina Reader

1. Sign up at [Jina Reader API](https://jina.ai/reader/)
2. Set `JINA_API_KEY` in `.env`

## Firecrawl

1. Sign up at [Firecrawl](https://www.firecrawl.dev/)
2. Set `FIRECRAWL_API_KEY` in `.env`
3. Or run self-hosted with `docker-compose.firecrawl.yml`

## FlareSolverr (Self-Hosted)

No API key needed. Run as a Docker sidecar:

```yaml
services:
  flaresolverr:
    image: ghcr.io/flaresolverr/flaresolverr:latest
    ports:
      - "8191:8191"
    environment:
      - LOG_LEVEL=info
```

Add to `config.yaml`:

```yaml
providers:
  flaresolverr:
    base_url: http://flaresolverr:8191
    timeout: 120
```

## Zyte

1. Sign up at [Zyte](https://www.zyte.com/)
2. Copy your API key from the dashboard
3. Set `ZYTE_API_KEY` in `.env`
4. Add to `config.yaml`:

```yaml
providers:
  zyte:
    api_key: ${ZYTE_API_KEY}
    timeout: 120
```

## DuckDuckGo

No API key or account needed. Just add to `config.yaml`:

```yaml
providers:
  duckduckgo:
    timeout: 15
```
