# Crawl4AI Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Crawl4AI as a self-hosted content extraction provider with two modes — `crawl4ai` (full browser crawl via `POST /crawl`) and `crawl4ai_md` (lightweight markdown via `POST /md`) — both backed by a single sidecar container, composable via the existing policy engine and fallback chains.

**Architecture:** One adapter class `Crawl4AIAdapter` with a `mode` parameter (`"crawl"` or `"md"`). Registered twice in the provider registry as `crawl4ai` and `crawl4ai_md`, pointing at the same sidecar container (`unclecode/crawl4ai:0.8.6`, port 11235). Users control routing through policy rules (domain/URL-pattern matching), fallback chains, or per-request `provider` override — no new routing code.

**Tech Stack:** Python 3.12, httpx (async), Pydantic config, Docker Compose sidecar, pytest

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/webgateway/providers/crawl4ai.py` | Create | `Crawl4AIAdapter` class (extract-only, two modes) |
| `src/webgateway/providers/registry.py` | Modify | Import + two `_create_adapter` branches (`crawl4ai`, `crawl4ai_md`) |
| `src/webgateway/providers/__init__.py` | Modify | Add crawl4ai to docstring |
| `config.yaml` | Modify | Add `crawl4ai` + `crawl4ai_md` provider blocks |
| `config.test.yaml` | Modify | Add `crawl4ai` + `crawl4ai_md` provider blocks |
| `docker-compose.yml` | Modify | Add `crawl4ai` sidecar service |
| `docker-compose.test.yml` | Modify | Add `crawl4ai` sidecar service (profile-gated) |
| `tests/integration/test_crawl4ai.py` | Create | Integration tests (extract via gateway, health, metadata) |
| `tests/integration/conftest.py` | Modify | Add `crawl4ai_available` skip fixture |

---

### Task 1: Create Crawl4AI Adapter

**Files:**
- Create: `src/webgateway/providers/crawl4ai.py`

- [ ] **Step 1: Write the adapter**

```python
"""Crawl4AI content extraction provider adapter.

Crawl4AI (https://github.com/unclecode/crawl4ai) is an open-source
self-hosted crawler with a built-in FastAPI server. It runs as a sidecar
container and exposes two extraction endpoints:

- ``POST /crawl`` — full browser crawl with JS rendering. Returns
  markdown, cleaned HTML, extracted JSON, and metadata. Slower but handles
  JS-heavy sites.
- ``POST /md`` — lightweight markdown extraction. Faster for simple
  pages, no full browser rendering.

The same adapter class handles both modes via the ``mode`` parameter.
It is registered twice in the provider registry — once as ``crawl4ai``
(mode="crawl") and once as ``crawl4ai_md`` (mode="md") — so users can
compose them independently through policy rules and fallback chains.
"""

from __future__ import annotations

import httpx

from webgateway.providers.base import (
    ExtractOptions,
    ExtractResult,
    ProviderError,
    ProviderMetadata,
    SearchOptions,
    SearchResult,
)

__all__ = ["Crawl4AIAdapter"]


class Crawl4AIAdapter:
    """Adapter for a self-hosted Crawl4AI instance.

    Parameters
    ----------
    base_url:
        URL of the Crawl4AI server (default ``http://crawl4ai:11235``).
    timeout:
        Default timeout in seconds for extraction requests.
    mode:
        ``"crawl"`` uses ``POST /crawl`` (full browser rendering).
        ``"md"`` uses ``POST /md`` (lightweight markdown).
    api_token:
        Optional bearer token for Crawl4AI auth (only needed when
        bound to ``0.0.0.0``).
    """

    def __init__(
        self,
        base_url: str = "http://crawl4ai:11235",
        timeout: int = 30,
        *,
        mode: str = "crawl",
        api_token: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._mode = mode
        self._api_token = api_token

    # ------------------------------------------------------------------
    # ProviderAdapter protocol
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "crawl4ai_md" if self._mode == "md" else "crawl4ai"

    @property
    def metadata(self) -> ProviderMetadata:
        name = self.name
        if self._mode == "md":
            return ProviderMetadata(
                name=name,
                self_hosted=True,
                data_retention_days=0,
                trains_on_queries=False,
                gdpr_compliant=True,
                data_residency=["local"],
                capabilities=["extract"],
                specialization="markdown",
                cost_units_per_call=0.3,
            )
        return ProviderMetadata(
            name=name,
            self_hosted=True,
            data_retention_days=0,
            trains_on_queries=False,
            gdpr_compliant=True,
            data_residency=["local"],
            capabilities=["extract"],
            specialization="browser",
            cost_units_per_call=0.5,
        )

    async def search(
        self, query: str, options: SearchOptions
    ) -> SearchResult:
        """Crawl4AI does not support web search."""
        raise ProviderError(self.name, "Crawl4AI does not support search")

    async def extract(
        self, url: str, options: ExtractOptions
    ) -> ExtractResult:
        """Extract content from *url* via Crawl4AI.

        Dispatches to ``POST /crawl`` (mode="crawl") or ``POST /md``
        (mode="md") depending on the adapter mode.
        """
        if self._mode == "md":
            return await self._extract_md(url, options)
        return await self._extract_crawl(url, options)

    async def health_check(self) -> bool:
        """Check whether the Crawl4AI server is reachable."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self._base_url}/health")
                return resp.status_code < 400
        except httpx.HTTPError:
            return False

    # ------------------------------------------------------------------
    # Internal: /crawl endpoint (full browser crawl)
    # ------------------------------------------------------------------

    async def _extract_crawl(
        self, url: str, options: ExtractOptions
    ) -> ExtractResult:
        headers = _build_headers(self._api_token)
        payload = {
            "urls": [url],
            "crawler_config": {
                "cache_mode": "bypass",
                "word_count_threshold": 10,
            },
        }
        try:
            async with httpx.AsyncClient(
                proxy=options.proxy_url,
                timeout=self._timeout + 30,
            ) as client:
                resp = await client.post(
                    f"{self._base_url}/crawl",
                    headers=headers,
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise ProviderError(
                self.name, f"Request failed: {exc}"
            ) from exc

        if resp.status_code >= 400:
            raise ProviderError(
                self.name,
                f"Crawl4AI returned HTTP {resp.status_code}",
                status_code=resp.status_code,
            )

        data = resp.json()
        results = data.get("results", [])
        if not results:
            raise ProviderError(
                self.name, "Crawl4AI returned empty results"
            )

        result = results[0]
        if not result.get("success", False):
            raise ProviderError(
                self.name,
                f"Crawl failed: {result.get('error_message', 'unknown error')}",
            )

        metadata = result.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}

        # Prefer fit_markdown (cleaner), fall back to markdown.
        content = _coerce_str(
            result.get("fit_markdown") or result.get("markdown") or ""
        )
        title = _coerce_optional_str(metadata.get("title"))

        return ExtractResult(
            content=content,
            format="markdown",
            url=_coerce_str(result.get("url", url)),
            title=title,
            status_code=int(result.get("status_code", resp.status_code)),
        )

    # ------------------------------------------------------------------
    # Internal: /md endpoint (lightweight markdown)
    # ------------------------------------------------------------------

    async def _extract_md(
        self, url: str, options: ExtractOptions
    ) -> ExtractResult:
        headers = _build_headers(self._api_token)
        payload = {
            "urls": [url],
            "browser_config": {
                "text_mode": True,
                "headless": True,
            },
        }
        try:
            async with httpx.AsyncClient(
                proxy=options.proxy_url,
                timeout=self._timeout + 10,
            ) as client:
                resp = await client.post(
                    f"{self._base_url}/md",
                    headers=headers,
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise ProviderError(
                self.name, f"Request failed: {exc}"
            ) from exc

        if resp.status_code >= 400:
            raise ProviderError(
                self.name,
                f"Crawl4AI returned HTTP {resp.status_code}",
                status_code=resp.status_code,
            )

        data = resp.json()
        results = data.get("results", [])
        if not results:
            raise ProviderError(
                self.name, "Crawl4AI returned empty results"
            )

        result = results[0]
        if not result.get("success", False):
            raise ProviderError(
                self.name,
                f"Markdown extraction failed: "
                f"{result.get('error_message', 'unknown error')}",
            )

        metadata = result.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}

        content = _coerce_str(result.get("markdown") or "")
        title = _coerce_optional_str(metadata.get("title"))

        return ExtractResult(
            content=content,
            format="markdown",
            url=_coerce_str(result.get("url", url)),
            title=title,
            status_code=int(result.get("status_code", resp.status_code)),
        )


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _build_headers(api_token: str | None) -> dict[str, str]:
    """Return common request headers."""
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_token:
        headers["Authorization"] = f"Bearer {api_token}"
    return headers


def _coerce_str(value: object) -> str:
    """Return *value* as a str."""
    return str(value) if value else ""


def _coerce_optional_str(value: object) -> str | None:
    """Return *value* as a str, or None if it is falsy/None."""
    if value is None:
        return None
    text = str(value)
    return text or None
```

- [ ] **Step 2: Run lint**

Run: `source .venv/bin/activate && ruff check src/webgateway/providers/crawl4ai.py`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add src/webgateway/providers/crawl4ai.py
git commit -m "feat: add Crawl4AI adapter with crawl and md modes"
```

---

### Task 2: Wire Into Provider Registry

**Files:**
- Modify: `src/webgateway/providers/registry.py:16-24` (imports)
- Modify: `src/webgateway/providers/registry.py:130-137` (create branches)

- [ ] **Step 1: Add import**

In `registry.py`, add the import after line 24 (`from webgateway.providers.tavily import TavilyAdapter`):

```python
from webgateway.providers.crawl4ai import Crawl4AIAdapter
```

- [ ] **Step 2: Add creation branches**

In `_create_adapter`, after the `exa` block (line 134), before the `logger.warning` line (line 136):

```python
        if name == "crawl4ai":
            return Crawl4AIAdapter(
                base_url=cfg.base_url or "http://crawl4ai:11235",
                timeout=cfg.timeout or 30,
                mode="crawl",
                api_token=cfg.api_key,
            )
        if name == "crawl4ai_md":
            return Crawl4AIAdapter(
                base_url=cfg.base_url or "http://crawl4ai:11235",
                timeout=cfg.timeout or 30,
                mode="md",
                api_token=cfg.api_key,
            )
```

- [ ] **Step 3: Run lint**

Run: `source .venv/bin/activate && ruff check src/webgateway/providers/registry.py`
Expected: No errors

- [ ] **Step 4: Commit**

```bash
git add src/webgateway/providers/registry.py
git commit -m "feat: register crawl4ai and crawl4ai_md in provider registry"
```

---

### Task 3: Update Package Docstring

**Files:**
- Modify: `src/webgateway/providers/__init__.py`

- [ ] **Step 1: Update docstring**

Replace the full file with:

```python
"""Provider adapters for search and extraction services.

Includes cloud providers (Jina, Brave, Tavily, Firecrawl, Context7,
Perplexity), self-hosted services (SearXNG, DevDocs, Crawl4AI), and
browser-based adapters (invisible_playwright).
"""
```

This removes the stale references to `playwright` and `flaresolverr` that have no adapter files, and adds `Crawl4AI`.

- [ ] **Step 2: Run lint**

Run: `source .venv/bin/activate && ruff check src/webgateway/providers/__init__.py`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add src/webgateway/providers/__init__.py
git commit -m "chore: update providers docstring, remove stale playwright/flaresolverr refs"
```

---

### Task 4: Add Config Entries

**Files:**
- Modify: `config.yaml:146-148` (after exa block)
- Modify: `config.test.yaml:67-68` (after exa block)

- [ ] **Step 1: Add to config.yaml**

After the `exa` provider block (line 148), add:

```yaml
  # Self-hosted browser crawler — full JS rendering
  # Requires: docker compose -f docker-compose.yml up crawl4ai
  crawl4ai:
    base_url: http://crawl4ai:11235
    timeout: 30
    cost_units_per_call: 0.5
    specialization: browser

  # Self-hosted lightweight markdown extraction (same container as crawl4ai)
  crawl4ai_md:
    base_url: http://crawl4ai:11235
    timeout: 30
    cost_units_per_call: 0.3
    specialization: markdown
```

Also update the default fallback chain (line 18-21) to include `crawl4ai_md`:

```yaml
    fallback_chain:
      - jina
      - crawl4ai_md
      - firecrawl
```

- [ ] **Step 2: Add to config.test.yaml**

After the `exa` provider block (line 68), add:

```yaml
  crawl4ai:
    base_url: http://crawl4ai:11235
    timeout: 30
    enabled: true
    specialization: browser
    cost_units_per_call: 0.5
  crawl4ai_md:
    base_url: http://crawl4ai:11235
    timeout: 30
    enabled: true
    specialization: markdown
    cost_units_per_call: 0.3
```

- [ ] **Step 3: Commit**

```bash
git add config.yaml config.test.yaml
git commit -m "feat: add crawl4ai and crawl4ai_md provider config entries"
```

---

### Task 5: Add Docker Compose Sidecar

**Files:**
- Modify: `docker-compose.yml` (add `crawl4ai` service)

- [ ] **Step 1: Add crawl4ai sidecar to docker-compose.yml**

After the `searxng` service block (line 30), add:

```yaml
  # --- Crawl4AI self-hosted crawler (JS rendering + markdown) ---
  crawl4ai:
    image: unclecode/crawl4ai:0.8.6
    ports:
      - "11235:11235"
    shm_size: "1gb"
    environment:
      CRAWL4AI_API_TOKEN: ${CRAWL4AI_API_TOKEN:-}
    volumes:
      - crawl4ai-cache:/home/appuser/.cache
    deploy:
      resources:
        limits:
          memory: 4g
        reservations:
          memory: 1g
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:11235/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s
    restart: unless-stopped
    networks:
      - webgateway-net
```

Also add `crawl4ai-cache` to the `volumes:` section at the bottom (after `cache-data:`):

```yaml
volumes:
  cache-data:
  crawl4ai-cache:
```

- [ ] **Step 2: Add crawl4ai to docker-compose.test.yml**

After the `searxng` service block (line 38), add:

```yaml
  # --- Crawl4AI self-hosted crawler ---
  crawl4ai:
    image: unclecode/crawl4ai:0.8.6
    profiles: ["crawl4ai"]
    ports:
      - "11235:11235"
    shm_size: "1gb"
    environment:
      CRAWL4AI_API_TOKEN: ""
    deploy:
      resources:
        limits:
          memory: 4g
        reservations:
          memory: 1g
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:11235/health"]
      interval: 15s
      timeout: 10s
      retries: 5
      start_period: 40s
    restart: "no"
    networks:
      - webgateway-net
```

Also add `crawl4ai-cache` to the `volumes:` section:

```yaml
volumes:
  cache-data:
  crawl4ai-cache:
```

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml docker-compose.test.yml
git commit -m "feat: add crawl4ai sidecar container to docker compose"
```

---

### Task 6: Add Integration Tests

**Files:**
- Create: `tests/integration/test_crawl4ai.py`
- Modify: `tests/integration/conftest.py` (add skip fixture)

- [ ] **Step 1: Add skip fixture to conftest.py**

After line 214 (`exa_available = _make_provider_skip_fixture("exa", "Exa")`), add:

```python
crawl4ai_available = _make_provider_skip_fixture("crawl4ai", "Crawl4AI")
crawl4ai_md_available = _make_provider_skip_fixture("crawl4ai_md", "Crawl4AI MD")
```

- [ ] **Step 2: Create integration test file**

```python
"""Integration tests for Crawl4AI through the WebGateway.

Requires the Crawl4AI sidecar running:
    docker compose -f docker-compose.test.yml --profile crawl4ai up -d

Tests auto-skip when the sidecar isn't running.
"""

from __future__ import annotations

import httpx

EXAMPLE_COM_URL = "https://example.com"


class TestCrawl4AIExtract:
    """Tests for crawl4ai (full browser crawl mode)."""

    def test_extract_returns_content(
        self, client: httpx.Client, auth_headers, crawl4ai_available: None
    ):
        r = client.post(
            "/extract",
            json={
                "url": EXAMPLE_COM_URL,
                "format": "markdown",
                "provider": "crawl4ai",
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["provider_used"] == "crawl4ai"
        assert data["url"] == EXAMPLE_COM_URL
        assert len(data["content"]) > 0

    def test_extract_response_schema(
        self, client: httpx.Client, auth_headers, crawl4ai_available: None
    ):
        r = client.post(
            "/extract",
            json={"url": EXAMPLE_COM_URL, "provider": "crawl4ai"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        for field in (
            "content",
            "format",
            "url",
            "provider_used",
            "request_id",
            "latency_ms",
            "cached",
        ):
            assert field in data, f"Missing required field: {field}"

    def test_search_raises_error(
        self, client: httpx.Client, auth_headers, crawl4ai_available: None
    ):
        r = client.post(
            "/search",
            json={"query": "test", "provider": "crawl4ai"},
            headers=auth_headers,
        )
        # ProviderError should trigger fallback; 422 if provider
        # doesn't support search and no fallback is configured.
        assert r.status_code in (422, 502)


class TestCrawl4AIMdExtract:
    """Tests for crawl4ai_md (lightweight markdown mode)."""

    def test_extract_returns_markdown(
        self, client: httpx.Client, auth_headers, crawl4ai_md_available: None
    ):
        r = client.post(
            "/extract",
            json={
                "url": EXAMPLE_COM_URL,
                "format": "markdown",
                "provider": "crawl4ai_md",
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["provider_used"] == "crawl4ai_md"
        assert data["format"] == "markdown"
        assert len(data["content"]) > 0

    def test_extract_response_schema(
        self, client: httpx.Client, auth_headers, crawl4ai_md_available: None
    ):
        r = client.post(
            "/extract",
            json={"url": EXAMPLE_COM_URL, "provider": "crawl4ai_md"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        for field in (
            "content",
            "format",
            "url",
            "provider_used",
            "request_id",
            "latency_ms",
            "cached",
        ):
            assert field in data, f"Missing required field: {field}"


class TestCrawl4AIMetadata:
    def test_crawl4ai_metadata(
        self, client: httpx.Client, auth_headers, crawl4ai_available: None
    ):
        r = client.get("/providers", headers=auth_headers)
        assert r.status_code == 200
        providers = r.json()
        c4 = next(p for p in providers if p["name"] == "crawl4ai")
        assert c4["self_hosted"] is True
        assert "extract" in c4["capabilities"]
        assert "search" not in c4["capabilities"]

    def test_crawl4ai_md_metadata(
        self, client: httpx.Client, auth_headers, crawl4ai_md_available: None
    ):
        r = client.get("/providers", headers=auth_headers)
        assert r.status_code == 200
        providers = r.json()
        c4md = next(p for p in providers if p["name"] == "crawl4ai_md")
        assert c4md["self_hosted"] is True
        assert "extract" in c4md["capabilities"]
        assert "search" not in c4md["capabilities"]

    def test_crawl4ai_and_md_are_separate_providers(
        self, client: httpx.Client, auth_headers, crawl4ai_available: None
    ):
        r = client.get("/providers", headers=auth_headers)
        providers = r.json()
        names = [p["name"] for p in providers]
        assert "crawl4ai" in names
        assert "crawl4ai_md" in names
```

- [ ] **Step 3: Run lint**

Run: `source .venv/bin/activate && ruff check tests/integration/test_crawl4ai.py tests/integration/conftest.py`
Expected: No errors

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_crawl4ai.py tests/integration/conftest.py
git commit -m "test: add integration tests for crawl4ai and crawl4ai_md"
```

---

### Task 7: Add Post-Processing Exemption

**Files:**
- Modify: `config.yaml:334-342` (post_processing.providers section)

- [ ] **Step 1: Add crawl4ai post-processing entries**

In `config.yaml`, inside the `post_processing.providers` section (after the `jina` block, around line 342), add:

```yaml
    crawl4ai:
      stage1_extractor: none
      stage2_converter: none
      stage3_clean: true
    crawl4ai_md:
      stage1_extractor: none
      stage2_converter: none
      stage3_clean: true
```

Crawl4AI already returns clean markdown — skip trafilatura extraction and markdownify conversion, but keep boilerplate cleaning.

Also update `config.test.yaml` post_processing.providers section (after the `jina` block around line 177):

```yaml
    crawl4ai:
      stage1_extractor: none
      stage2_converter: none
      stage3_clean: true
    crawl4ai_md:
      stage1_extractor: none
      stage2_converter: none
      stage3_clean: true
```

- [ ] **Step 2: Commit**

```bash
git add config.yaml config.test.yaml
git commit -m "feat: add crawl4ai post-processing config (skip extract/convert, keep clean)"
```

---

### Task 8: Update Documentation

**Files:**
- Modify: `docs-src/docs/providers/extract.md` (add Crawl4AI section)

- [ ] **Step 1: Add Crawl4AI to extract docs**

Read the existing `docs-src/docs/providers/extract.md` first to match the existing format and structure. Then add a Crawl4AI section following the same pattern as the other providers. Include:
  - Provider overview (self-hosted, JS rendering, two modes)
  - Docker setup instructions (sidecar image, shm_size requirement)
  - Config example showing both `crawl4ai` and `crawl4ai_md` entries
  - Policy rule examples showing domain-based routing between modes
  - Example API calls for both modes

- [ ] **Step 2: Commit**

```bash
git add docs-src/docs/providers/extract.md
git commit -m "docs: add Crawl4AI provider documentation"
```

---

### Task 9: Verify — Full Stack Smoke Test

**Files:** None (verification only)

- [ ] **Step 1: Start the stack**

Run:
```bash
docker compose -f docker-compose.yml -f docker-compose.test.yml --profile crawl4ai up -d --build
```
Wait for all services to be healthy.

- [ ] **Step 2: Run unit tests**

Run: `make test-unit`
Expected: All pass (including any new unit tests)

- [ ] **Step 3: Run integration tests (crawl4ai only)**

Run:
```bash
source .venv/bin/activate && pytest tests/integration/test_crawl4ai.py -v
```
Expected: All pass (crawl4ai sidecar running)

- [ ] **Step 4: Run full integration suite**

Run: `make test-integration`
Expected: All pass (existing tests unaffected)

- [ ] **Step 5: Run lint**

Run: `make lint`
Expected: No errors

- [ ] **Step 6: Test provider metadata endpoint**

Run:
```bash
curl -s http://localhost:8080/providers -H "Authorization: Bearer test-agent-key" | python3 -m json.tool | grep -A5 crawl4ai
```
Expected: Both `crawl4ai` and `crawl4ai_md` appear with correct metadata

- [ ] **Step 7: Test extraction**

Run:
```bash
curl -s -X POST http://localhost:8080/extract \
  -H "Authorization: Bearer test-agent-key" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "provider": "crawl4ai"}' | python3 -m json.tool
```
Expected: 200 with markdown content

Run:
```bash
curl -s -X POST http://localhost:8080/extract \
  -H "Authorization: Bearer test-agent-key" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "provider": "crawl4ai_md"}' | python3 -m json.tool
```
Expected: 200 with markdown content

- [ ] **Step 8: Tear down**

Run:
```bash
docker compose -f docker-compose.yml -f docker-compose.test.yml --profile crawl4ai down -v
```
