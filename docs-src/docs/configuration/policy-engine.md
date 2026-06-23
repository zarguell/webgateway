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

## Structured Data Extraction

Policy rules can enable per-domain extraction strategies that enrich extract responses with structured metadata (ratings, prices, genres, dates) alongside the full page content. Strategies run in priority order — the first to produce data wins.

```yaml
  - name: imdb
    match:
      domain_glob: "*.imdb.com"
    extract_strategy:
      priority:
        - json_ld         # Try JSON-LD first
        - meta_extract     # Then meta tags
        - article_extract  # Default trafilatura fallback
```

### How it works

1. Provider returns raw HTML
2. Strategy selector runs on the HTML, looking for the configured strategies in priority order
3. If a strategy finds structured data (e.g., JSON-LD with `@type: Movie`), it's returned in the `structured_data` field of the extract response
4. The full page content still flows through the normal trafilatura pipeline — strategies **supplement**, they never replace content

### Response

Every extract response has a `structured_data` field — always present (null when no strategy matched):

```json
{
  "content": "Full page text extracted by trafilatura...",
  "format": "markdown",
  "structured_data": {
    "@type": "Movie",
    "name": "The Shawshank Redemption",
    "aggregateRating": {"ratingValue": "9.3", "reviewCount": "2684145"},
    "duration": "PT2H22M",
    "genre": ["Drama"],
    "datePublished": "1994-10-14"
  }
}
```

### Available strategies

| Strategy | Extracts | Best for |
|---|---|---|
| `json_ld` | `<script type="application/ld+json">` blocks, scored by `@type` priority | Product, Movie, Recipe, Event, Article pages |
| `meta_extract` | Open Graph, Twitter Card, and standard `<meta>` tags | News articles, blog posts, any page with OG tags |
| `article_extract` | Default trafilatura → markdownify | Everything (always the final fallback) |

### Configured domains

These domains have `json_ld` → `meta_extract` → `article_extract` priority configured:

| Domain | Provider | Notes |
|---|---|---|---|
| `*.imdb.com` | CDP Chrome (default) | ✅ Verified — `@type: Movie`, rating, duration, genre |
| `*.amazon.com`, `*.amazon.*` | invisible_playwright | ⚠️ meta_extract works, Product JSON-LD not static |
| `*.ebay.com` | invisible_playwright | ⚠️ meta_extract works |
| `*.etsy.com` | flaresolverr | ⚠️ CAPTCHA bypassed, thin JS-rendered content |
| `*.bestbuy.com` | flaresolverr | ⚠️ CAPTCHA bypassed, thin JS-rendered content |
| `*.walmart.com` | flaresolverr | ⚠️ CAPTCHA bypassed, thin JS-rendered content |
| `*.rottentomatoes.com` | invisible_playwright | ✅ Verified — `@type: Movie`, rating, cast, genre |
| `*.goodreads.com` | invisible_playwright | ✅ Verified — `@type: Book`, ISBN, pages, author |
| `stackoverflow.com`, `*.stackoverflow.com`, `*.stackexchange.com` | invisible_playwright | ✅ Verified — Organization structured data |

Add more by adding policy rules with `extract_strategy` to your `config.yaml`.

Append `?dry_run=true` to `/search` or `/extract` to see what the policy engine would decide without executing the request. Useful for debugging routing rules and verifying fallback chains.

```bash
# Preview search routing
curl -X POST "http://localhost:8080/search?dry_run=true" \
  -H "Authorization: Bearer $AGENT1_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query": "machine learning papers", "num_results": 5}'

# Preview extract routing
curl -X POST "http://localhost:8080/extract?dry_run=true" \
  -H "Authorization: Bearer $AGENT1_KEY" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.reddit.com/r/python"}'
```

Response shows the full routing decision:

```json
{
  "decision": {
    "policy_matched": "reddit",
    "provider": "invisible_playwright",
    "proxy": "gluetun",
    "fallback_chain": ["firecrawl", "jina"],
    "retry_strategy": "fallback",
    "dlp_policy": null,
    "judge_invoked": false,
    "judge_reasoning_tag": null
  },
  "request_id": "req_a1b2c3"
}
```

**Fields:**

| Field | Description |
|-------|-------------|
| `policy_matched` | Name of the matched policy rule, or `null` if no rule matched (uses defaults) |
| `provider` | Provider selected for this request |
| `proxy` | Named proxy, or `null` if no proxy |
| `fallback_chain` | Ordered list of fallback providers |
| `retry_strategy` | Retry strategy from the matched rule |
| `dlp_policy` | DLP policy applied, or `null` |
| `judge_invoked` | Whether the LLM judge was consulted for this decision |
| `judge_reasoning_tag` | Tag from the LLM judge explaining its reasoning |
