# Policy Engine — Rule Syntax

Policies are YAML rules evaluated in order. First match wins.

## Match Criteria

```yaml
policies:
  - name: reddit
    match:
      domain: "*.reddit.com"         # Exact domain or glob
      domain_glob: ["*.wsj.com"]     # List of domain globs
      url_pattern: ".*/pricing"      # URL regex
      api_key_id: "key_agent1"       # Specific API key
      content_type: "search"         # "search" or "extract"
      query_contains: ["diagnosis"]  # Search query keywords
      on_error_class: ["403", "bot_detected"]  # Error-based fallback
```

## Actions

```yaml
  - name: paywalled_news
    match:
      domain_glob: ["*.wsj.com", "*.nytimes.com"]
    extract_provider: playwright    # Override provider
    search_provider: brave          # Override search provider
    proxy: gluetun                  # Route through proxy
    playwright_profile: wsj_session # Use specific session
    fallback_chain:                 # Custom fallback order
      - playwright
      - firecrawl
    dlp_policy: no_cloud_health     # Apply DLP policy
    allowed_providers:              # Restrict to these
      - searxng
      - crawl4ai
```
