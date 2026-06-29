# Extraction Strategies — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-domain extraction strategy selector to the post-processing pipeline, starting with `json_ld` strategy. Strategy selection is policy-driven. Structured data is surfaced in REST `format=json` responses.

**Architecture:** Strategy selector runs on raw HTML before the existing 5-stage pipeline. If a strategy produces content, stages 1-2 (trafilatura extraction + markdown conversion) are skipped — the strategy output is already clean text. Stages 3-5 (cleaning, dedup, injection detect) still run. For MCP/`format=markdown`, structured data is flattened to readable text. For REST/`format=json`, the raw JSON-LD is returned in a new `structured_data` field.

**Tech Stack:** Python `json` stdlib, Pydantic for config models, existing post-processing pipeline.

**Design doc:** `docs/superpowers/specs/2026-06-23-extraction-strategies-design.md`

---

### Task 1: Add config model for extraction strategies

**Files:**
- Modify: `src/serp_llm/config.py` — add `ExtractStrategyConfig` and wire into `PolicyRule`

- [ ] **Step 1: Add ExtractStrategyConfig model**

Add to `src/serp_llm/config.py`, after the `PolicyRule` class (around line 89):

```python
class ExtractStrategyConfig(BaseModel):
    """Configuration for per-domain extraction strategies."""
    priority: list[str] = ["article_extract"]
```

- [ ] **Step 2: Add extract_strategy field to PolicyRule**

Add to `PolicyRule` class (line 88, after `allowed_providers`):

```python
    extract_strategy: ExtractStrategyConfig | None = None
```

- [ ] **Step 3: Run lint to verify**

```bash
source .venv/bin/activate && ruff check src/serp_llm/config.py
```

Expected: clean

- [ ] **Step 4: Commit**

```bash
git add src/serp_llm/config.py
git commit -m "feat(config): add ExtractStrategyConfig model to PolicyRule"
```

---

### Task 2: Create strategy registry and selector

**Files:**
- Create: `src/serp_llm/postprocessing/strategies/__init__.py`

- [ ] **Step 1: Write the strategy interface and selector**

Create `src/serp_llm/postprocessing/strategies/__init__.py`:

```python
"""Extraction strategy registry and selector.

Strategies are tried in priority order (configured per-domain in policy rules).
The first strategy to return non-empty content wins. If no strategy produces
content, the default trafilatura pipeline is used.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from serp_llm.config import ConfigManager

logger = logging.getLogger(__name__)


@dataclass
class StrategyResult:
    """Result from a single extraction strategy."""

    content: str
    format: str = "markdown"
    structured_data: dict | list | None = None


class ExtractionStrategy(Protocol):
    """Interface for individual extraction strategies."""

    async def extract(self, html: str, url: str) -> StrategyResult | None:
        """Return extracted content or None if strategy cannot handle the page."""
        ...


class StrategySelector:
    """Selects and runs extraction strategies based on policy config.

    Usage:
        selector = StrategySelector(config_manager)
        result = await selector.run(html, url, policy_matched_name)
        if result:
            # Use result.content instead of trafilatura output
            pipeline.stage_1_and_2_skip = True
    """

    def __init__(self, config_manager: ConfigManager) -> None:
        self._config = config_manager
        self._strategies: dict[str, ExtractionStrategy] = {}

    def register(self, name: str, strategy: ExtractionStrategy) -> None:
        """Register a named strategy."""
        self._strategies[name] = strategy

    async def run(
        self,
        html: str,
        url: str,
        policy_matched: str | None,
    ) -> StrategyResult | None:
        """Run strategies in priority order for the matched policy rule.

        Returns the first non-empty result, or ``None`` if no strategy matched
        (caller should fall back to default trafilatura pipeline).
        """
        if not policy_matched:
            return None

        # Find the matching policy rule
        rule = None
        for r in self._config.config.policies:
            if r.name == policy_matched:
                rule = r
                break

        if rule is None or rule.extract_strategy is None:
            return None

        for strategy_name in rule.extract_strategy.priority:
            if strategy_name == "article_extract":
                continue  # article_extract is the default fallback, skip here
            strategy = self._strategies.get(strategy_name)
            if strategy is None:
                logger.debug("Strategy %r not registered, skipping", strategy_name)
                continue
            try:
                result = await strategy.extract(html, url)
                if result is not None and result.content.strip():
                    logger.debug(
                        "Strategy %r produced content for %s", strategy_name, url
                    )
                    return result
            except Exception:
                logger.exception("Strategy %r failed for %s", strategy_name, url)
                continue

        return None
```

- [ ] **Step 2: Run lint**

```bash
source .venv/bin/activate && ruff check src/serp_llm/postprocessing/strategies/__init__.py
```

Expected: clean

- [ ] **Step 3: Commit**

```bash
git add src/serp_llm/postprocessing/strategies/__init__.py
git commit -m "feat(pipeline): add extraction strategy registry and selector"
```

---

### Task 3: Implement json_ld extraction strategy

**Files:**
- Create: `src/serp_llm/postprocessing/strategies/json_ld.py`

- [ ] **Step 1: Write the json_ld strategy**

Create `src/serp_llm/postprocessing/strategies/json_ld.py`:

```python
"""JSON-LD extraction strategy.

Extracts all ``<script type="application/ld+json">`` blocks from the HTML,
parses each as JSON, and selects the most relevant one by ``@type`` priority.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from serp_llm.postprocessing.strategies import ExtractionStrategy, StrategyResult

logger = logging.getLogger(__name__)

# Priority ordering for JSON-LD @type values. Higher = more relevant.
_TYPE_PRIORITY: dict[str, int] = {
    "Product": 100,
    "Recipe": 90,
    "JobPosting": 85,
    "Event": 80,
    "Movie": 75,
    "Book": 75,
    "Article": 70,
    "NewsArticle": 70,
    "TechArticle": 70,
    "SoftwareApplication": 65,
    "LocalBusiness": 60,
    "Organization": 50,
    "Person": 50,
    "Review": 45,
    "FAQPage": 40,
    "WebPage": 10,
    "WebSite": 5,
    "BreadcrumbList": 3,
    "SearchAction": 1,
}

_JSONLD_RE = re.compile(
    r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


@dataclass
class _ScoredBlock:
    data: dict
    score: int
    type_name: str


def _score_block(data: dict) -> _ScoredBlock | None:
    """Score a JSON-LD block by its @type and field count."""
    type_val = data.get("@type", "")
    if isinstance(type_val, str):
        primary = type_val
    elif isinstance(type_val, list) and type_val:
        primary = type_val[0]
    else:
        return None

    base_score = _TYPE_PRIORITY.get(primary, 20)
    field_count = len(data) - 1  # exclude @context
    score = base_score + min(field_count, 50)
    return _ScoredBlock(data=data, score=score, type_name=primary)


def _flatten_to_markdown(data: dict) -> str:
    """Flatten a JSON-LD block into readable markdown."""
    lines: list[str] = []
    type_name = data.get("@type", "")
    if isinstance(type_name, list):
        type_name = type_name[0]

    name = data.get("name", data.get("headline", ""))
    if name:
        lines.append(f"# {name}")

    desc = data.get("description", "")
    if desc:
        lines.append("")
        lines.append(desc)

    # Price
    offers = data.get("offers")
    if isinstance(offers, dict):
        price = offers.get("price")
        currency = offers.get("priceCurrency", "")
        if price is not None:
            lines.append("")
            lines.append(f"**Price:** {currency} {price}")
    elif isinstance(offers, list) and offers:
        prices = [o.get("price") for o in offers if isinstance(o, dict)]
        if prices:
            lines.append("")
            lines.append(f"**Price range:** {' – '.join(str(p) for p in prices if p)}")

    # Rating
    agg_rating = data.get("aggregateRating")
    if isinstance(agg_rating, dict):
        rating = agg_rating.get("ratingValue")
        count = agg_rating.get("reviewCount")
        if rating:
            suffix = f" ({count} reviews)" if count else ""
            lines.append(f"**Rating:** {rating}/5{suffix}")

    # Availability
    if offers:
        availability = (
            offers.get("availability") if isinstance(offers, dict) else None
        )
        if availability:
            in_stock = "InStock" in str(availability) or "InStock" in str(availability)
            lines.append(f"**Availability:** {'In Stock' if in_stock else 'Check'}")

    # Author
    author = data.get("author")
    if isinstance(author, dict):
        author_name = author.get("name")
        if author_name:
            lines.append(f"**Author:** {author_name}")

    # Date
    date = data.get("datePublished")
    if date:
        lines.append(f"**Published:** {date}")

    # Key-value pairs for remaining known fields
    for key in ("sku", "brand", "mpn", "isbn"):
        val = data.get(key)
        if val:
            if isinstance(val, dict):
                val = val.get("name", str(val))
            lines.append(f"**{key.capitalize()}:** {val}")

    return "\n".join(lines).strip()


class JsonLdStrategy:
    """Extract structured data from JSON-LD script blocks."""

    async def extract(self, html: str, url: str) -> StrategyResult | None:
        """Find and extract the best JSON-LD block from *html*."""
        matches = _JSONLD_RE.findall(html)
        if not matches:
            return None

        candidates: list[_ScoredBlock] = []
        for raw in matches:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            # Handle @graph (list of blocks)
            blocks = data if isinstance(data, list) else [data]
            for block in blocks:
                if isinstance(block, dict) and "@type" in block:
                    scored = _score_block(block)
                    if scored:
                        candidates.append(scored)

        if not candidates:
            return None

        candidates.sort(key=lambda c: c.score, reverse=True)
        best = candidates[0]
        markdown = _flatten_to_markdown(best.data)

        return StrategyResult(
            content=markdown,
            format="markdown",
            structured_data=best.data,
        )
```

- [ ] **Step 2: Run lint**

```bash
source .venv/bin/activate && ruff check src/serp_llm/postprocessing/strategies/json_ld.py
```

Expected: clean

- [ ] **Step 3: Commit**

```bash
git add src/serp_llm/postprocessing/strategies/json_ld.py
git commit -m "feat(pipeline): add json_ld extraction strategy"
```

---

### Task 4: Wire strategy selector into post-processing pipeline

**Files:**
- Modify: `src/serp_llm/post_processing/pipeline.py` — add strategy selector call before stage 1
- Modify: `src/serp_llm/service.py` — pass policy_matched to pipeline, surface structured_data

- [ ] **Step 1: Modify PostProcessingResult to carry structured_data**

Add to `src/serp_llm/post_processing/pipeline.py` in `PostProcessingResult` (after `injection`):

```python
    structured_data: dict | list | None = None
```

- [ ] **Step 2: Add strategy selector to PostProcessingPipeline.__init__**

After the existing imports in `pipeline.py`, add:

```python
from serp_llm.postprocessing.strategies import StrategySelector
```

In `__init__`, add parameter and field:

```python
    def __init__(
        self,
        config: PostProcessingConfig,
        *,
        strategy_selector: StrategySelector | None = None,
        dedup_store: DedupStore | None = None,
        injection_detector: InjectionDetector | None = None,
    ) -> None:
        ...
        self._strategy_selector = strategy_selector
```

- [ ] **Step 3: Run strategy selector in pipeline.run()**

In `pipeline.py`, add after the docstring in `run()` and before Stage 1 (before line 65):

```python
        # Stage 0: Extraction strategy (policy-driven, runs before trafilatura)
        structured_data: dict | list | None = None
        if (
            self._strategy_selector is not None
            and format == "html"
        ):
            strategy_result = await self._strategy_selector.run(
                content, url, policy_matched=provider,
            )
            if strategy_result is not None:
                content = strategy_result.content
                format = strategy_result.format
                structured_data = strategy_result.structured_data
                # Skip stages 1-2 (strategy output is already clean)
                extractor = "none"
                converter = "none"
```

This needs a bit of care — the `provider` parameter in `run()` is the provider name (e.g. "cdp_chrome"), but we need the policy rule name. Let me add a `policy_matched` parameter to `run()` instead.

Change the signature:

```python
    async def run(
        self,
        content: str,
        url: str,
        *,
        format: str = "html",
        provider: str | None = None,
        policy_matched: str | None = None,
        skip_injection: bool = False,
    ) -> PostProcessingResult:
```

And in Stage 0 use `policy_matched` instead of `provider`:

```python
            strategy_result = await self._strategy_selector.run(
                content, url, policy_matched=policy_matched,
            )
```

Also update the final return to include structured_data:

```python
        return PostProcessingResult(
            content=markdown,
            format="markdown",
            structured_data=structured_data,
            ...
        )
```

- [ ] **Step 4: Pass policy_matched from service.py**

In `src/serp_llm/service.py`, find the `pp_result = await self._post_processing.run(...)` call (line 661) and add `policy_matched=decision.policy_matched`:

```python
            pp_result = await self._post_processing.run(
                content=result.content,
                url=request.url,
                format=result.format,
                provider=provider_used,
                policy_matched=decision.policy_matched,
                skip_injection=skip_injection,
            )
```

- [ ] **Step 5: Surface structured_data in ExtractResponse**

Also in `service.py`, after the pp_result is processed (around line 668-670), capture structured_data:

```python
            result.content = pp_result.content
            result.format = pp_result.format
            structured_data = pp_result.structured_data
```

Then in the `ExtractResponse` construction (around line 811), add `structured_data`:

```python
        response = ExtractResponse(
            content=result.content,
            format=result.format if request.format != "json" else "json",
            url=request.url,
            provider_used=provider_used,
            structured_data=structured_data if request.format == "json" else None,
            ...
        )
```

Only include `structured_data` when the request asked for JSON format.

- [ ] **Step 6: Initialize strategy selector in app factory**

Find where `PostProcessingPipeline` is constructed in `main.py` or `service.py`, and pass the strategy selector:

```python
from serp_llm.postprocessing.strategies import StrategySelector
from serp_llm.postprocessing.strategies.json_ld import JsonLdStrategy

# In the initialization:
strategy_selector = StrategySelector(config_manager)
strategy_selector.register("json_ld", JsonLdStrategy())

# Then pass to PostProcessingPipeline:
self._post_processing = PostProcessingPipeline(
    config=post_processing_config,
    strategy_selector=strategy_selector,
    ...
)
```

- [ ] **Step 7: Run lint**

```bash
source .venv/bin/activate && ruff check src/serp_llm/post_processing/pipeline.py src/serp_llm/service.py src/serp_llm/schemas.py
```

Expected: clean

- [ ] **Step 8: Commit**

```bash
git add src/serp_llm/post_processing/pipeline.py src/serp_llm/service.py src/serp_llm/schemas.py
git commit -m "feat(pipeline): wire strategy selector into post-processing pipeline"
```

---

### Task 5: Add structured_data field to ExtractResponse schema

**Files:**
- Modify: `src/serp_llm/schemas.py` — add `structured_data` field

- [ ] **Step 1: Add field to ExtractResponse**

In `src/serp_llm/schemas.py`, add to `ExtractResponse` (after `prompt_injection`):

```python
    structured_data: dict | list | None = None
```

- [ ] **Step 2: Verify**

```bash
source .venv/bin/activate && ruff check src/serp_llm/schemas.py
```

Expected: clean

- [ ] **Step 3: Commit** (if not already included in Task 4)

---

### Task 6: Write unit tests

**Files:**
- Create: `tests/unit/test_strategies.py`

- [ ] **Step 1: Write tests for json_ld strategy**

Create `tests/unit/test_strategies.py`:

```python
from __future__ import annotations

import pytest

from serp_llm.postprocessing.strategies.json_ld import JsonLdStrategy


@pytest.fixture
def strategy() -> JsonLdStrategy:
    return JsonLdStrategy()


class TestJsonLdStrategy:
    async def test_extract_product_json_ld(self, strategy: JsonLdStrategy):
        html = """
        <html><head>
        <script type="application/ld+json">
        {
            "@context": "https://schema.org",
            "@type": "Product",
            "name": "Test Product",
            "description": "A test product",
            "offers": {
                "@type": "Offer",
                "price": "29.99",
                "priceCurrency": "USD",
                "availability": "https://schema.org/InStock"
            },
            "aggregateRating": {
                "@type": "AggregateRating",
                "ratingValue": "4.5",
                "reviewCount": "123"
            },
            "brand": {"@type": "Brand", "name": "TestBrand"},
            "sku": "TST-001"
        }
        </script>
        </head><body><p>Some content</p></body></html>
        """
        result = await strategy.extract(html, "https://example.com/product")
        assert result is not None
        assert "Test Product" in result.content
        assert "29.99" in result.content
        assert "4.5" in result.content
        assert "In Stock" in result.content
        assert result.structured_data is not None
        assert result.structured_data["@type"] == "Product"

    async def test_no_json_ld_returns_none(self, strategy: JsonLdStrategy):
        html = "<html><body><p>No structured data here</p></body></html>"
        result = await strategy.extract(html, "https://example.com")
        assert result is None

    async def test_empty_json_ld_returns_none(self, strategy: JsonLdStrategy):
        html = """
        <html><head>
        <script type="application/ld+json"></script>
        </head></html>
        """
        result = await strategy.extract(html, "https://example.com")
        assert result is None

    async def test_malformed_json_ld_ignored(self, strategy: JsonLdStrategy):
        html = """
        <html><head>
        <script type="application/ld+json">{invalid</script>
        <script type="application/ld+json">
        {"@type": "Product", "name": "Valid Product"}
        </script>
        </head></html>
        """
        result = await strategy.extract(html, "https://example.com")
        assert result is not None
        assert "Valid Product" in result.content

    async def test_article_type_scored_lower_than_product(
        self, strategy: JsonLdStrategy
    ):
        html = """
        <html><head>
        <script type="application/ld+json">
        {"@type": "WebPage", "name": "Generic Page"}
        </script>
        <script type="application/ld+json">
        {"@type": "Product", "name": "Real Product", "description": "desc"}
        </script>
        </head></html>
        """
        result = await strategy.extract(html, "https://example.com")
        assert result is not None
        assert "Real Product" in result.content

    async def test_article_extract_flat_markdown(self, strategy: JsonLdStrategy):
        """Verify the markdown flattening is readable."""
        html = """
        <html><head>
        <script type="application/ld+json">
        {
            "@type": "Recipe",
            "name": "Test Recipe",
            "description": "A delicious recipe",
            "author": {"@type": "Person", "name": "Chef"},
            "datePublished": "2024-01-15"
        }
        </script>
        </head></html>
        """
        result = await strategy.extract(html, "https://example.com/recipe")
        assert result is not None
        assert "# Test Recipe" in result.content
        assert "Chef" in result.content
        assert "2024-01-15" in result.content

    async def test_strategy_result_has_structured_data(
        self, strategy: JsonLdStrategy
    ):
        html = """
        <html><head>
        <script type="application/ld+json">
        {"@type": "Product", "name": "JSON Product", "price": "9.99"}
        </script>
        </head></html>
        """
        result = await strategy.extract(html, "https://example.com/p")
        assert result is not None
        assert result.structured_data is not None
        assert result.structured_data["name"] == "JSON Product"

    async def test_multiple_types_in_list(self, strategy: JsonLdStrategy):
        html = """
        <html><head>
        <script type="application/ld+json">
        {"@type": ["Product", "Book"], "name": "Multi-Type Item", "isbn": "123"}
        </script>
        </head></html>
        """
        result = await strategy.extract(html, "https://example.com/multi")
        assert result is not None
        assert "Multi-Type Item" in result.content
```

- [ ] **Step 2: Run tests**

```bash
source .venv/bin/activate && pytest tests/unit/test_strategies.py -v
```

Expected: all 8+ tests pass

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_strategies.py
git commit -m "test: json_ld extraction strategy unit tests"
```

---

### Task 7: Add amazon.com policy rule to configs

**Files:**
- Modify: `config.yaml` — add amazon.com policy with json_ld strategy
- Modify: `config.local.yaml` — same

- [ ] **Step 1: Add to config.yaml**

Add after the existing wikipedia policy (or at the end of the `policies:` section):

```yaml
  - name: amazon-product
    match:
      domain: "*.amazon.com"
    extract_strategy:
      priority:
        - json_ld
        - article_extract
```

- [ ] **Step 2: Add to config.local.yaml**

Same block:

```yaml
  - name: amazon-product
    match:
      domain: "*.amazon.com"
    extract_strategy:
      priority:
        - json_ld
        - article_extract
```

- [ ] **Step 3: Commit**

```bash
git add config.yaml config.local.yaml
git commit -m "feat(config): add amazon.com policy with json_ld extraction strategy"
```

---

### Task 8: Integration smoke test

**Files:** None (test via serpLLM's own MCP)

- [ ] **Step 1: Rebuild gateway and start local stack**

```bash
bash scripts/launch-chrome-cdp.sh
docker compose -f docker-compose.local.yml --profile local up -d --build
```

- [ ] **Step 2: Test json_ld extraction on amazon.com**

```bash
curl -s -X POST http://localhost:8080/extract \
  -H "Authorization: Bearer local-agent-key" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.amazon.com/dp/XXXX", "provider": "cdp_chrome"}'
```

Expected: response has structured data from JSON-LD. Content is clean markdown with product info, not raw HTML.

- [ ] **Step 3: Test with format=json**

```bash
curl -s -X POST http://localhost:8080/extract \
  -H "Authorization: Bearer local-agent-key" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.amazon.com/dp/XXXX", "provider": "cdp_chrome", "format": "json"}'
```

Expected: `structured_data` field present with raw JSON-LD object.

- [ ] **Step 4: Run full unit test suite**

```bash
source .venv/bin/activate && pytest tests/unit/ -v --tb=short
```

Expected: all existing tests pass + new strategy tests pass.

---

## Execution Order

```
Task 1: Config model
  ↓
Task 2: Strategy registry
  ↓
Task 3: JSON-LD strategy
  ↓
Task 5: Schema changes      ← needed before pipeline wiring
  ↓
Task 4: Wire into pipeline
  ↓
Task 6: Unit tests
  ↓
Task 7: Config rules
  ↓
Task 8: Smoke test
```
