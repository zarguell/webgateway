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
