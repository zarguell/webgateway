# Adding a New Provider

End-to-end checklist for implementing a new search or extract provider. Every file that needs to be created or modified is listed below.

## Decision Tree

Before writing code, answer these questions:

### 1. What capabilities does the provider support?

| Capability | Required methods | Example |
|---|---|---|
| **Search only** | `search()`, `extract()` raises | SearXNG, Brave, Tavily, DuckDuckGo |
| **Extract only** | `extract()`, `search()` raises | Jina, Firecrawl, FlareSolverr, Zyte |
| **Both** | `search()` and `extract()` both return results | Exa |

### 2. How is the provider hosted?

| Type | Config pattern | Examples |
|---|---|---|
| **Self-hosted** | `base_url`, no API key | SearXNG, Crawl4AI, FlareSolverr, DevDocs |
| **Cloud API** | `api_key` required | Brave, Tavily, Jina, Firecrawl, Zyte, Exa |
| **Free public** | No key, no host | DuckDuckGo |

### 3. Does it need a new Python dependency?

| Approach | When to use |
|---|---|
| **httpx** (existing) | Provider has a REST API — most cases |
| **New package** | Provider has a purpose-built SDK (e.g., `ddgs` for DuckDuckGo) |
| **Selenium/playwright** | Provider requires a real browser (rare — prefer cloud APIs) |

### 4. Is it async?

All provider adapters **must** be async. If the upstream library is synchronous (like `ddgs`), wrap calls with `asyncio.to_thread()`.

---

## Implementation Checklist

### Step 1: Create the adapter

**File:** `src/serp_llm/providers/<name>.py`

Follow this skeleton exactly:

```python
"""One-line description of what the provider does and how it's called.
"""

from __future__ import annotations

import httpx  # if using httpx directly

from serp_llm.providers.base import (
    ExtractOptions,
    ExtractResult,
    ProviderError,
    ProviderMetadata,
    ResultItem,           # only if search-capable
    SearchOptions,        # only if search-capable
    SearchResult,         # only if search-capable
)

__all__ = ["<Name>Adapter"]


class <Name>Adapter:
    """Adapter for <Provider>."""

    def __init__(self, <params>) -> None:
        self._timeout = timeout
        self._api_key = api_key
        self._base_url = base_url

    @property
    def name(self) -> str:
        return "<name>"  # must match the config key

    @property
    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            name="<name>",
            self_hosted=<bool>,
            data_retention_days=<int | None>,
            trains_on_queries=<bool | None>,
            gdpr_compliant=<bool>,
            data_residency=["<region>"],
            privacy_policy_url="<url>" | None,
            capabilities=["search"] | ["extract"] | ["search", "extract"],
            specialization="<optional category>",
            cost_units_per_call=<float>,
        )

    async def search(self, query: str, options: SearchOptions) -> SearchResult:
        """Return normalised search results."""
        # ... httpx call or SDK call ...
        # Map upstream fields → ResultItem(title, url, snippet, published_date)
        return SearchResult(results=results)

    async def extract(self, url: str, options: ExtractOptions) -> ExtractResult:
        """Return extracted content."""
        # ... httpx call or SDK call ...
        return ExtractResult(content=html, format="markdown", url=url)

    async def health_check(self) -> bool:
        """Return True if the provider is reachable."""
        # Use key-presence check for cloud APIs (avoids billing):
        #   return bool(self._api_key)
        # Use HTTP probe for self-hosted:
        #   return response.status_code < 400
        try:
            return True
        except Exception:
            return False
```

**Key rules:**

- Provider name (`self.name`) **must exactly match** the config key in `config.yaml`
- `search()` maps upstream fields to `ResultItem(title, url, snippet, published_date)`
- `extract()` returns `ExtractResult(content=..., format=..., url=...)`
- The unsupported method **must raise** `ProviderError("<name>", "<provider> does not support <action>")`
- Error handling: catch upstream exceptions → re-raise as `ProviderError` with appropriate `error_class` (`"rate_limited"`, `"timeout"`, `"auth_failed"`, `"bot_detected"`)
- Health check: prefer key-presence for cloud APIs (no billing hit), HTTP probe for self-hosted
- Use `options.proxy_url` when making httpx calls — pass it to the `proxy` parameter

### Step 2: Register in the provider registry

**File:** `src/serp_llm/providers/registry.py`

Add import at the top:

```python
from serp_llm.providers.<name> import <Name>Adapter
```

Add a branch in `_create_adapter()`:

```python
if name == "<name>":
    return <Name>Adapter(
        api_key=cfg.api_key,
        base_url=cfg.base_url or "<default_url>",
        timeout=cfg.timeout or 15,
    )
```

### Step 3: Add dependency (if needed)

**File:** `pyproject.toml`

Add to `dependencies` list:

```toml
"<package-name>=<version>",
```

Then install locally: `pip install "<package-name>=<version>"`

### Step 4: Add configuration

**File:** `config.yaml`

Add under `providers:` section. Follow the existing comment style:

```yaml
providers:
  # <One-line description of provider>
  <name>:
    api_key: ${<ENV_VAR>}      # cloud API, or omit for self-hosted/free
    base_url: <default_url>    # self-hosted, or omit for fixed cloud URL
    timeout: 15                # seconds
```

**File:** `config.test.yaml`

Add the provider block with `enabled: true` and test-safe defaults:

```yaml
  <name>:
    api_key: ${<ENV_VAR>:-}   # empty default, tests skip if no key
    timeout: 15
    enabled: true
    specialization: <category>
    cost_units_per_call: <float>
```

### Step 5: Post-processing configuration

**File:** `config.yaml` and `config.test.yaml` — under `post_processing.providers:`

Add an entry for the new provider to control how raw output is cleaned:

```yaml
post_processing:
  providers:
    <name>:
      stage1_extractor: none         # "none" if provider returns clean markdown
      stage2_converter: none         # "none" if provider returns markdown
      stage3_clean: true             # always true — clean boilerplate
```

- Providers that return raw HTML need `stage1_extractor: trafilatura` and `stage2_converter: markdownify`
- Providers that return clean markdown use `stage1_extractor: none` and `stage2_converter: none`
- `stage3_clean: true` is almost always correct

### Step 6: DLP policy integration

**File:** `config.yaml` — under `dlp_policies:`

If the provider is cloud-hosted and receives user queries, add it to the outbound DLP policy:

```yaml
dlp_policies:
  - name: no_pii_upstream
    applies_to_providers:
      - <name>   # add here
```

### Step 7: Unit tests

**File:** `tests/unit/test_<name>.py`

```python
from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock  # if using httpx

from serp_llm.providers.base import ProviderError
from serp_llm.providers.<name> import <Name>Adapter


@pytest.fixture
def adapter() -> <Name>Adapter:
    return <Name>Adapter(api_key="test-key", timeout=15)
```

**Required test cases:**

| Test | What it verifies |
|---|---|
| `test_name` | `adapter.name == "<name>"` |
| `test_metadata` | capabilities, self_hosted, specialization |
| `test_search_success` | Mock HTTP response → returns `SearchResult` with correct fields |
| `test_search_no_key` | Missing API key → raises `ProviderError` |
| `test_search_rate_limit` | HTTP 429 → raises with `error_class="rate_limited"` |
| `test_search_auth_error` | HTTP 401/403 → raises with `error_class="auth_failed"` |
| `test_search_generic_error` | Network failure → raises `ProviderError` |
| `test_extract_success` | Mock HTTP response → returns `ExtractResult` with content |
| `test_extract_not_supported` | Raises `ProviderError` for search-only providers |
| `test_health_check_ok` | Returns `True` |
| `test_health_check_failure` | Returns `False` on error |
| `test_proxy_passthrough` | `options.proxy_url` is forwarded to the HTTP client |

**Mocking approach:**

- **httpx-based providers:** use `pytest_httpx` (`httpx_mock.add_response(...)`) — mock at HTTP level
- **SDK-based providers:** use `unittest.mock.patch` to mock the SDK class
- **Sync SDKs wrapped in `asyncio.to_thread`:** mock the SDK class, not the HTTP layer

**Pattern for httpx mocks:**

```python
async def test_search_success(self, adapter, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        method="POST",
        url="https://api.example.com/search",
        json={"results": [{"title": "Test", "url": "https://example.com", "snippet": "..."}]},
    )
    result = await adapter.search("test query", options=SearchOptions(num_results=1))
    assert len(result.results) == 1
    assert result.results[0].title == "Test"
```

### Step 8: Integration test fixture

**File:** `tests/integration/conftest.py`

Add rate limit delay (if cloud/rate-limited):

```python
_RATE_LIMIT_DELAYS: dict[str, float] = {
    "<name>": 1.0,
}
```

Add auto-skip fixture:

```python
<name>_available = _make_provider_skip_fixture("<name>", "<HumanName>")
```

### Step 9: Integration tests

**File:** `tests/integration/test_<name>.py`

```python
class Test<Name>Search:
    def test_search_returns_results(self, client, auth_headers, <name>_available):
        r = client.post("/search", json={
            "query": "test query", "num_results": 3, "provider": "<name>",
        }, headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["provider_used"] == "<name>"
        assert len(r.json()["results"]) > 0

    def test_search_result_items_have_required_fields(self, client, auth_headers, <name>_available):
        # ... verify title, url, snippet in each result

    def test_search_response_has_metadata(self, client, auth_headers, <name>_available):
        # ... verify request_id, latency_ms

    def test_search_request_id_in_response_header(self, client, auth_headers, <name>_available):
        # ... verify x-request-id header matches body


class Test<Name>ProviderMetadata:
    def test_<name>_appears_in_providers(self, client, auth_headers, <name>_available):
        r = client.get("/providers", headers=auth_headers)
        providers = r.json()
        provider = next(p for p in providers if p["name"] == "<name>")
        assert "<search>" in provider["capabilities"]
```

Integration tests auto-skip when the provider isn't healthy (no API key, network unreachable).

### Step 10: Docker setup (self-hosted only)

**File:** `docker-compose.test.yml`

Add a service with the `flaresolverr`-style pattern (profile-based, optional):

```yaml
services:
  <name>:
    image: <image>:<tag>
    ports:
      - "<port>:<port>"
    profiles:
      - <name>    # opt-in: docker compose --profile <name> up
```

### Step 11: Documentation

**Three files to update:**

1. **`docs-src/docs/providers/search.md`** or **`extract.md`** — Add a `## ProviderName` section with:
   - One-line description
   - `### Configuration` — YAML block
   - `### Docker Setup` — only for self-hosted providers
   - `### Policy Routing` — example policy rule
   - `### API Calls` — curl example

2. **`docs-src/docs/configuration/provider-setup.md`** — Add a `## ProviderName` setup guide with:
   - Signup/install instructions
   - API key instructions (cloud) or Docker instructions (self-hosted)
   - Config snippet

3. **`docs-src/docs/providers/data-policies.md`** — No manual edit needed. This table is auto-generated from `ProviderMetadata`. Ensure the adapter's `metadata` property has all fields populated correctly, then regenerate with `scripts/generate_provider_pages.py`.

### Step 12: Verification

Run in order:

```bash
# 1. Lint
make lint

# 2. Unit tests (no Docker)
make test-unit

# 3. Integration tests (Docker required)
# Rebuild the gateway first (code + dependency changes):
docker compose -f docker-compose.test.yml up -d --build
pytest tests/integration/test_<name>.py -v
```

All three must pass before the provider is done.

---

## File Change Summary

| # | File | Action |
|---|---|---|
| 1 | `src/serp_llm/providers/<name>.py` | **Create** — adapter implementation |
| 2 | `src/serp_llm/providers/registry.py` | **Modify** — import + `_create_adapter()` branch |
| 3 | `pyproject.toml` | **Modify** — add dependency (if new package needed) |
| 4 | `config.yaml` | **Modify** — provider block + DLP policy + post-processing |
| 5 | `config.test.yaml` | **Modify** — provider block + post-processing |
| 6 | `tests/unit/test_<name>.py` | **Create** — 12+ unit tests |
| 7 | `tests/integration/conftest.py` | **Modify** — rate limit + skip fixture |
| 8 | `tests/integration/test_<name>.py` | **Create** — 5 integration tests |
| 9 | `docker-compose.test.yml` | **Modify** — Docker service (self-hosted only) |
| 10 | `docs-src/docs/providers/search.md` or `extract.md` | **Modify** — provider section |
| 11 | `docs-src/docs/configuration/provider-setup.md` | **Modify** — setup guide |

---

## Naming Convention

- Tool calls and API endpoints use **`extract`**, never "scrape"
- Provider name in code: **`<name>`** (lowercase, underscores)
- Provider name in config: **`<name>`** (must match code)
- Test file: **`test_<name>.py`**
- Fixture name: **`<name>_available`**

---

## Reference Adapters

Use these as templates — they cover the main patterns:

| Pattern | Reference adapter |
|---|---|
| Search-only, cloud API, httpx | `brave.py` or `tavily.py` |
| Search-only, self-hosted, httpx | `searxng.py` |
| Extract-only, cloud API, httpx | `zyte.py` or `jina.py` |
| Extract-only, self-hosted, httpx | `flaresolverr.py` |
| Both search + extract, cloud API | `exa.py` |
| Both modes, self-hosted, one container | `crawl4ai.py` |
| Search-only, SDK-based (async wrapper) | `duckduckgo.py` |
