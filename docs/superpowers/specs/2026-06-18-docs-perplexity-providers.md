# Docs + Perplexity Provider Adapters

**Date:** 2026-06-18
**Status:** Pre-implementation
**Implements:** PRD-addendum.md §19 (Docs Providers), PRD-addendum-2.md §20 (Provider additions)

---

## 1. Scope

Three new provider adapters for the WebGateway, all **search-only** (no extract/extraction):

| Provider | Specialization | Hosting | Docs source |
|----------|---------------|---------|-------------|
| Context7 | docs | Cloud (api.context7.com) | Library documentation, versioned |
| Perplexity | ai-native | Cloud (api.perplexity.ai) | Web search with AI synthesis |
| DevDocs | docs | Self-hosted (devdocs:9292) | 100+ aggregated library docs |

---

## 2. Provider: Context7

### 2.1 API

Two-phase REST API — no MCP client needed:

1. **`GET /api/v2/libs/search?libraryName=X&query=Y`** — resolve a library name to a Context7 library ID
2. **`GET /api/v2/context?libraryId=X&query=Y`** — fetch documentation snippets for the matched library

Base URL: `https://context7.com`
Auth: `Authorization: Bearer <key>` (optional — anonymous requests work with lower rate limits)

### 2.2 Search Flow

```
search("FastAPI middleware", options)
  → libs/search(libraryName="FastAPI", query="middleware")
  → pick best match by benchmarkScore
  → context(libraryId="/fastapi/fastapi", query="middleware")
  → return codeSnippets + infoSnippets as SearchResult results[]
```

Library name extraction from query: use the first token that looks like a library name (capitalized word, common library names list). Fallback: pass the entire query as both `libraryName` and `query` and let Context7 resolve it.

### 2.3 Response Mapping

```python
# Context7 codeSnippet → ResultItem
ResultItem(
    title=snippet.codeTitle or snippet.pageTitle,
    url=snippet.codeId.split("#")[0],  # strip fragment
    snippet=f"{snippet.codeDescription}\n```{snippet.codeList[0].language}\n{snippet.codeList[0].code}\n```",
)

# Context7 infoSnippet → ResultItem
ResultItem(
    title=snippet.breadcrumb or snippet.pageId,
    url=snippet.pageId,
    snippet=snippet.content[:300],
)
```

### 2.4 Metadata

```python
ProviderMetadata(
    name="context7",
    self_hosted=False,
    data_retention_days=None,
    trains_on_queries=False,
    gdpr_compliant=True,
    hipaa_compliant=False,
    data_residency=["US", "EU"],
    privacy_policy_url="https://context7.com/privacy",
    mcp_native=True,
    capabilities=["search"],
    specialization="docs",
    cost_units_per_call=0.0,
)
```

### 2.5 Health Check

`GET /api/v2/libs/search?libraryName=test&query=test` — return `True` if 200, `False` otherwise.

---

## 3. Provider: Perplexity

### 3.1 API

`POST https://api.perplexity.ai/v1/sonar`

OpenAI Chat Completions format. Key response fields for search:
- `citations[]` — source URLs
- `search_results[]` — structured results: title, url, snippet, date
- `choices[0].message.content` — AI-synthesized answer

Auth: `Authorization: Bearer <key>` (required)

### 3.2 Search Flow

```
search("latest AI research", options)
  → POST /v1/sonar with model="sonar-pro", messages=[{role:"user", content:query}]
  → parse citations[] + search_results[] + content
  → return SearchResult with:
      results[].title = search_result.title or "Source [n]"
      results[].url = search_result.url or citation
      results[].snippet = search_result.snippet or excerpt from content
```

### 3.3 Request Parameters

```json
{
  "model": "sonar-pro",
  "messages": [{"role": "user", "content": "<query>"}],
  "web_search_options": {"search_context_size": "medium"},
  "return_images": false,
  "return_related_questions": false,
  "stream": false
}
```

`model` configurable via `ProviderConfig.specialization` override or adapter init param. Default: `sonar-pro`.

### 3.4 Metadata

```python
ProviderMetadata(
    name="perplexity",
    self_hosted=False,
    trains_on_queries=True,
    gdpr_compliant=False,     # US-based, data used for training unless opted out
    data_retention_days=None,
    hipaa_compliant=False,
    data_residency=["US"],
    privacy_policy_url="https://www.perplexity.ai/privacy",
    mcp_native=False,
    capabilities=["search"],
    specialization="ai_native",
    cost_units_per_call=0.1,
)
```

### 3.5 Health Check

`POST /v1/sonar` with a minimal query and expect 200/401/403. Do NOT consume tokens — check for auth error response, treat 401/403 as "configured but may be working" (return True if the endpoint responds).

### 3.6 Rate Limiting

Tier 0 (no spend): 50 RPM. On 429, `asyncio.sleep(1.2)` before retry once, following the Brave pattern.

---

## 4. Provider: DevDocs

### 4.1 API

Self-hosted at `http://devdocs:9292`. REST API:

- `GET /api/docs` — list available documentation sets
- `GET /api/docs/<slug>/search?q=<query>` — search a specific doc set (requires knowing the slug)

Simpler approach for v1: search all docs via the main search endpoint:
- `GET /search?q=<query>` (the web UI endpoint returns HTML — not ideal)
- Better: `GET /api/docs/search?q=<query>` (if available)

**Known limitation**: DevDocs's API varies by version. The self-hosted `ghcr.io/free-cn/devdocs` may not expose the same API as devdocs.io. Implementation will use the most common DevDocs API pattern and degrade gracefully.

### 4.2 Search Flow

```
search("Python list comprehension", options)
  → GET /api/docs/search?q=Python+list+comprehension
  → parse JSON response (format: [{name, path, docset, excerpt}])
  → return SearchResult with:
      results[].title = result.name
      results[].url = http://devdocs:9292{result.path}
      results[].snippet = result.excerpt
```

If `/api/docs/search` is unavailable, fall back to listing docsets via `/api/docs` and searching each. For v1, just try the single endpoint and return empty results on failure.

### 4.3 Metadata

```python
ProviderMetadata(
    name="devdocs",
    self_hosted=True,
    data_retention_days=None,
    trains_on_queries=False,
    gdpr_compliant=True,
    hipaa_compliant=True,
    data_residency=["local"],
    privacy_policy_url=None,
    mcp_native=False,
    capabilities=["search"],
    specialization="docs",
    cost_units_per_call=0.0,
)
```

### 4.4 Health Check

`GET /api/docs` — return `True` if 200, `False` otherwise.

---

## 5. Registry Changes

In `src/webgateway/providers/registry.py` `_create_adapter()`:

```python
if name == "context7":
    return Context7Adapter(api_key=config.api_key, timeout=config.timeout or 15)
if name == "perplexity":
    return PerplexityAdapter(api_key=config.api_key, timeout=config.timeout or 15)
if name == "devdocs":
    return DevDocsAdapter(
        base_url=config.base_url or "http://devdocs:9292",
        timeout=config.timeout or 15,
    )
```

All three search-only; `extract()` raises `ProviderError(name, "does not support extraction")`.

---

## 6. Config Additions

### config.yaml

```yaml
providers:
  context7:
    api_key: ${CONTEXT7_API_KEY:-}
    enabled: true
    specialization: docs
    cost_units_per_call: 0.0
  perplexity:
    api_key: ${PERPLEXITY_API_KEY:-}
    enabled: true
    specialization: ai_native
    cost_units_per_call: 0.1
  devdocs:
    base_url: http://devdocs:9292
    enabled: true
    specialization: docs
    cost_units_per_call: 0.0
```

### config.test.yaml

Same entries, all enabled with empty/default keys.

---

## 7. Docker Compose (DevDocs)

Add to `docker-compose.test.yml`:

```yaml
devdocs:
  image: ghcr.io/free-cn/devdocs:latest
  ports:
    - "9292:9292"
  restart: "no"
  healthcheck:
    test: ["CMD", "wget", "--spider", "-q", "http://localhost:9292/api/docs"]
    interval: 10s
    timeout: 5s
    retries: 10
    start_period: 15s
```

DevDocs is a standard service — no depends_on needed for webgateway since DevDocs is optional (graceful degradation).

---

## 8. Testing

### Unit Tests

Each adapter gets `tests/unit/test_{name}.py` using `pytest-httpx`:

| Test | What it covers |
|------|---------------|
| `test_search_success` | Happy path with real response fixture |
| `test_search_empty` | Empty results |
| `test_search_api_error` | 401/403/500 handling |
| `test_health_check_ok` | Healthy endpoint |
| `test_health_check_fail` | Unhealthy endpoint |
| `test_extract_unsupported` | Raises ProviderError |

### Integration Tests

Each adapter gets `tests/integration/test_{name}.py`:

- Auto-skips when provider not healthy (via `conftest.py`)
- Tests `POST /search` with the new provider
- Verifies response schema

### Rate-Limit Delays

```python
# conftest.py additions
_RATE_LIMIT_DELAYS = {
    "perplexity": 1.2,  # Tier 0: 50 RPM
    # Context7 and DevDocs don't need delays
}
```

---

## 9. File Manifest

| File | Action |
|------|--------|
| `src/webgateway/providers/context7.py` | Create |
| `src/webgateway/providers/perplexity.py` | Create |
| `src/webgateway/providers/devdocs.py` | Create |
| `src/webgateway/providers/registry.py` | Edit (3 imports + 3 if-blocks) |
| `src/webgateway/providers/__init__.py` | Edit (docstring update) |
| `config.yaml` | Edit (3 provider blocks) |
| `config.test.yaml` | Edit (3 provider blocks) |
| `docker-compose.test.yml` | Edit (DevDocs service) |
| `tests/unit/test_context7.py` | Create |
| `tests/unit/test_perplexity.py` | Create |
| `tests/unit/test_devdocs.py` | Create |
| `tests/integration/test_context7.py` | Create |
| `tests/integration/test_perplexity.py` | Create |
| `tests/integration/test_devdocs.py` | Create |
| `tests/integration/conftest.py` | Edit (auto-skip fixtures + rate-limit delays) |
