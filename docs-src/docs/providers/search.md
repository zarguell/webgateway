# Search Providers

## SearXNG

Self-hosted meta-search engine. No API key required. Routes queries through multiple upstream engines. Configured as the default search provider.

## Brave Search

Fast, privacy-respecting search API. Requires `BRAVE_API_KEY`. Rate limited to 1 request per second on the free tier.

## Tavily

AI-optimized search API designed for RAG and agent workloads. Returns clean, structured results. Requires `TAVILY_API_KEY`. 1000 queries/month on the free tier.

## DuckDuckGo

Free web search with no API key. Hits DuckDuckGo's lite interface directly. Rate-limited by DDG — not suitable for high-volume workloads. Good as a zero-cost fallback or backup search provider.

### Configuration

```yaml
providers:
  duckduckgo:
    timeout: 15
```

### Policy Routing

```yaml
policies:
  - name: free_search_fallback
    match:
      domain_glob: ["*.example.com"]
    search_provider: duckduckgo
```

### API Calls

```bash
curl -X POST http://localhost:8080/search \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query": "python async programming", "provider": "duckduckgo"}'
```
