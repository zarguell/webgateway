# Content Post-Processing Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a 4-stage post-processing pipeline that cleans raw HTML/markdown from scrape providers before returning content to the agent.

**Architecture:** New `src/serp_llm/post_processing/` module with staged pipeline (extraction → conversion → cleaning → dedup). Integrates into GatewayService.extract() between provider response and content quality validator. Per-provider config controls which stages run.

**Tech Stack:** Python 3.12+, trafilatura, readability-lxml, markdownify, html2text, sqlite3

**Dependencies already installed:** trafilatura, readability-lxml, markdownify, html2text

---

### Task 1: PostProcessingConfig model

**Files:**
- Modify: `src/serp_llm/config.py` (add PostProcessingConfig and related models)

- [ ] **Step 1: Add config models to config.py**

Add after `MCPConfig` (or near the end of config models, before `GatewayConfig`):

```python
class ExtractorConfig(BaseModel):
    """Per-provider pipeline stage overrides."""
    stage1_extractor: Literal["trafilatura", "readability", "none"] = "trafilatura"
    stage2_converter: Literal["markdownify", "html2text", "none"] = "markdownify"
    stage3_clean: bool = True
    stage4_deduplicate: bool = False


class CleaningConfig(BaseModel):
    min_content_length: int = 200
    additional_boilerplate_patterns: list[str] = Field(default_factory=list)


class DedupConfig(BaseModel):
    enabled: bool = False
    store: Literal["sqlite"] = "sqlite"


class PostProcessingConfig(BaseModel):
    default: ExtractorConfig = Field(default_factory=ExtractorConfig)
    providers: dict[str, ExtractorConfig] = Field(default_factory=dict)
    cleaning: CleaningConfig = Field(default_factory=CleaningConfig)
    deduplication: DedupConfig = Field(default_factory=DedupConfig)
```

Add to `GatewayConfig`:

```python
    post_processing: PostProcessingConfig = Field(default_factory=PostProcessingConfig)
```

- [ ] **Step 2: Verify config loads**

Run: `source .venv/bin/activate && python -c "from serp_llm.config import GatewayConfig; cfg = GatewayConfig(); print('post_processing default:', cfg.post_processing.default.model_dump())"`
Expected: prints default extractor config

- [ ] **Step 3: Run tests**

Run: `source .venv/bin/activate && pytest tests/unit/ -v --tb=short 2>&1 | tail -5`
Expected: 153 passed

- [ ] **Step 4: Commit**

```bash
git add src/serp_llm/config.py && git commit -m "feat: add PostProcessingConfig model"
```

---

### Task 2: Extractors module

**Files:**
- Create: `src/serp_llm/post_processing/__init__.py`
- Create: `src/serp_llm/post_processing/extractors.py`

- [ ] **Step 1: Create package init**

```python
"""Content post-processing pipeline — cleans raw HTML/markdown from scrape providers."""
```

- [ ] **Step 2: Create extractors.py**

```python
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def trafilatura_extract(html: str, url: str) -> str | None:
    """Extract main content using trafilatura. Returns markdown or None."""
    import trafilatura
    try:
        result = trafilatura.extract(
            html,
            url=url,
            output_format="markdown",
            include_links=True,
            include_images=False,
            include_tables=True,
            no_fallback=False,
        )
        return result if result else None
    except Exception as exc:
        logger.warning("trafilatura extraction failed for %s: %s", url, exc)
        return None


def readability_extract(html: str) -> str | None:
    """Extract main content using readability-lxml. Returns clean HTML or None."""
    from readability import Document
    try:
        doc = Document(html)
        summary = doc.summary()
        return summary if summary.strip() else None
    except Exception as exc:
        logger.warning("readability extraction failed: %s", exc)
        return None


def extract_main_content(
    html: str,
    url: str,
    *,
    extractor: str = "trafilatura",
    min_content_length: int = 200,
) -> tuple[str, str, bool]:
    """Run content extraction, return (content, format, used_fallback).

    Args:
        html: Raw HTML input.
        url: Source URL (used by trafilatura for link resolution).
        extractor: Which extractor to use ('trafilatura', 'readability', 'none').
        min_content_length: Minimum acceptable content length. Below this,
            extraction is considered failed and raw HTML is returned.

    Returns:
        Tuple of (extracted_content, content_format, used_fallback).
        content_format is 'markdown' if trafilatura succeeded, 'html' otherwise.
        used_fallback is True if readability was used as fallback.
    """
    if extractor == "none":
        return html, "html", False

    if extractor == "trafilatura":
        result = trafilatura_extract(html, url)
        if result is not None and len(result) >= min_content_length:
            return result, "markdown", False
        # trafilatura failed or returned too little — try readability fallback
        readability_result = readability_extract(html)
        if readability_result is not None and len(readability_result) >= min_content_length:
            return readability_result, "html", True
        # Both failed — return raw HTML with fallback flag
        logger.info(
            "extraction fallback for %s: trafilatura=%s readability=%s",
            url,
            len(result) if result else 0,
            len(readability_result) if readability_result else 0,
        )
        return html, "html", True

    if extractor == "readability":
        result = readability_extract(html)
        if result is not None and len(result) >= min_content_length:
            return result, "html", False
        return html, "html", True

    return html, "html", False
```

- [ ] **Step 3: Verify module imports**

Run: `source .venv/bin/activate && python -c "from serp_llm.post_processing.extractors import extract_main_content, trafilatura_extract, readability_extract; print('OK')"`
Expected: prints OK

- [ ] **Step 4: Commit**

```bash
git add src/serp_llm/post_processing/ && git commit -m "feat: add content extractors (trafilatura + readability)"
```

---

### Task 3: Converters module

**Files:**
- Create: `src/serp_llm/post_processing/converters.py`

- [ ] **Step 1: Create converters.py**

```python
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _is_html(content: str) -> bool:
    """Detect if content contains HTML tags (quick heuristic)."""
    return bool(_HTML_TAG_RE.search(content))


def markdownify_convert(html: str) -> str:
    """Convert HTML to markdown using markdownify."""
    from markdownify import markdownify as md
    try:
        return md(
            html,
            heading_style="ATX",
            bullets="-",
            strip=["script", "style", "nav", "footer", "header"],
        )
    except Exception as exc:
        logger.warning("markdownify conversion failed: %s", exc)
        return html


def html2text_convert(html: str) -> str:
    """Convert HTML to markdown using html2text (faster, simpler)."""
    import html2text
    try:
        h = html2text.HTML2Text()
        h.ignore_links = False
        h.ignore_images = True
        return h.handle(html)
    except Exception as exc:
        logger.warning("html2text conversion failed: %s", exc)
        return html


def convert_to_markdown(content: str, converter: str = "markdownify") -> str:
    """Convert content to markdown if it contains HTML. Skips if already markdown.

    Args:
        content: Input content (HTML or markdown).
        converter: Which converter to use ('markdownify', 'html2text', 'none').

    Returns:
        Markdown content. If input is already markdown (no HTML tags), returns as-is.
    """
    if converter == "none" or not _is_html(content):
        return content

    if converter == "markdownify":
        return markdownify_convert(content)
    if converter == "html2text":
        return html2text_convert(content)

    return content
```

- [ ] **Step 2: Verify**

Run: `source .venv/bin/activate && python -c "from serp_llm.post_processing.converters import convert_to_markdown, _is_html; assert _is_html('<p>test</p>'); assert not _is_html('just text'); print('OK')"`
Expected: prints OK

- [ ] **Step 3: Commit**

```bash
git add src/serp_llm/post_processing/converters.py && git commit -m "feat: add HTML-to-markdown converters"
```

---

### Task 4: Cleaners module

**Files:**
- Create: `src/serp_llm/post_processing/cleaners.py`

- [ ] **Step 1: Create cleaners.py**

```python
from __future__ import annotations

import re

# Default boilerplate patterns — applied to every cleaned markdown output.
_DEFAULT_BOILERPLATE_PATTERNS: list[str] = [
    r"(?i)^(cookie policy|accept cookies|privacy policy)\s*$",
    r"(?i)^(subscribe to (our )?newsletter)\s*$",
    r"(?i)^(share this article|share on)\s*.*$",
]


def clean_markdown(
    md: str,
    extra_patterns: list[str] | None = None,
) -> str:
    """Normalize markdown: collapse whitespace, remove boilerplate lines.

    Args:
        md: Markdown content to clean.
        extra_patterns: Additional regex patterns for boilerplate removal.

    Returns:
        Cleaned markdown string.
    """
    # Collapse 3+ consecutive newlines to 2
    md = re.sub(r"\n{3,}", "\n\n", md)

    # Strip lines that are only whitespace
    lines = md.splitlines()
    md = "\n".join(line for line in lines if line.strip() or line == "")

    # Combine default + extra boilerplate patterns
    patterns = list(_DEFAULT_BOILERPLATE_PATTERNS)
    if extra_patterns:
        patterns.extend(extra_patterns)

    # Remove matching lines (multi-line mode)
    for pattern in patterns:
        md = re.sub(pattern, "", md, flags=re.MULTILINE)

    # Clean up any double-newlines left by removed lines
    md = re.sub(r"\n{3,}", "\n\n", md)

    return md.strip()
```

- [ ] **Step 2: Verify**

Run: `source .venv/bin/activate && python -c "
from serp_llm.post_processing.cleaners import clean_markdown
# test whitespace collapse
assert 'a\n\nb' in clean_markdown('a\n\n\n\nb')
# test boilerplate removal
result = clean_markdown('Some content\nCookie Policy\nMore content')
assert 'Cookie Policy' not in result
assert 'More content' in result
print('OK')
"`
Expected: prints OK

- [ ] **Step 3: Commit**

```bash
git add src/serp_llm/post_processing/cleaners.py && git commit -m "feat: add markdown cleaner with boilerplate removal"
```

---

### Task 5: Dedup store

**Files:**
- Create: `src/serp_llm/post_processing/dedup.py`

- [ ] **Step 1: Create dedup.py**

```python
from __future__ import annotations

import hashlib
import time
from pathlib import Path

import sqlite3


class DedupStore:
    """SHA-256 content deduplication store.

    Tracks content hashes per URL to detect unchanged content on re-fetch.
    Opt-in — off by default (controlled by config.post_processing.deduplication.enabled).
    """

    def __init__(self, db_path: str = "data/dedup.db") -> None:
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS content_hashes (
                url_hash      TEXT PRIMARY KEY,
                content_hash  TEXT NOT NULL,
                ts            REAL NOT NULL
            );
        """)
        self._conn.commit()

    def _url_hash(self, url: str) -> str:
        return hashlib.sha256(url.encode()).hexdigest()

    @staticmethod
    def content_hash(content: str) -> str:
        return hashlib.sha256(content.encode()).hexdigest()

    async def check(self, url: str, content: str) -> tuple[str, bool]:
        """Check if *content* for *url* has changed since last seen.

        Returns:
            (content_hash, content_unchanged).
            content_unchanged is True when the hash matches the stored hash.
            The stored hash is always updated to the current value.
        """
        ch = self.content_hash(content)
        uh = self._url_hash(url)
        now = time.time()

        row = self._conn.execute(
            "SELECT content_hash FROM content_hashes WHERE url_hash = ?",
            (uh,),
        ).fetchone()

        unchanged = row is not None and row[0] == ch

        self._conn.execute(
            "INSERT OR REPLACE INTO content_hashes (url_hash, content_hash, ts) VALUES (?, ?, ?)",
            (uh, ch, now),
        )
        self._conn.commit()

        return ch, unchanged

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
```

- [ ] **Step 2: Verify**

Run: `source .venv/bin/activate && python -c "
import tempfile, os
from serp_llm.post_processing.dedup import DedupStore
import asyncio
async def test():
    store = DedupStore(tempfile.mktemp(suffix='.db'))
    ch1, unchanged1 = await store.check('https://example.com', 'hello world')
    assert not unchanged1  # first time
    ch2, unchanged2 = await store.check('https://example.com', 'hello world')
    assert unchanged2  # same content
    ch3, unchanged3 = await store.check('https://example.com', 'different')
    assert not unchanged3  # content changed
    store.close()
    print('OK')
asyncio.run(test())
"`
Expected: prints OK

- [ ] **Step 3: Commit**

```bash
git add src/serp_llm/post_processing/dedup.py && git commit -m "feat: add SHA-256 deduplication store"
```

---

### Task 6: Pipeline orchestrator

**Files:**
- Create: `src/serp_llm/post_processing/pipeline.py`

- [ ] **Step 1: Create pipeline.py**

```python
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from serp_llm.post_processing.cleaners import clean_markdown
from serp_llm.post_processing.converters import convert_to_markdown
from serp_llm.post_processing.dedup import DedupStore
from serp_llm.post_processing.extractors import extract_main_content

logger = logging.getLogger(__name__)


@dataclass
class PostProcessingResult:
    """Result of running the pipeline on a provider response."""

    content: str
    format: str = "markdown"  # always "markdown" after pipeline unless skipped
    extractor_used: str | None = None
    extraction_fallback: bool = False
    content_length_raw: int = 0
    content_length_processed: int = 0
    reduction_pct: float = 0.0
    content_unchanged: bool = False
    content_hash: str | None = None


class PostProcessingPipeline:
    """4-stage content post-processing pipeline.

    Orchestrates extraction → conversion → cleaning → dedup based on
    per-provider configuration from PostProcessingConfig.
    """

    def __init__(
        self,
        config: Any,  # PostProcessingConfig
        dedup_store: DedupStore | None = None,
    ) -> None:
        self._config = config
        self._dedup = dedup_store

    def _get_provider_config(self, provider: str) -> Any:
        """Return the ExtractorConfig for *provider*, falling back to default."""
        providers = getattr(self._config, "providers", {})
        if provider in providers:
            return providers[provider]
        return self._config.default

    async def run(
        self,
        content: str,
        url: str,
        *,
        format: str = "html",
        provider: str | None = None,
    ) -> PostProcessingResult:
        """Run the full pipeline on *content*.

        Args:
            content: Raw content from provider (HTML or markdown).
            url: Source URL for extraction.
            format: Input format ('html' or 'markdown').
            provider: Provider name for per-provider config lookup.

        Returns:
            PostProcessingResult with cleaned content and metadata.
        """
        raw_len = len(content)
        pcfg = self._get_provider_config(provider or "")

        # Stage 1: Main content extraction
        extractor = pcfg.stage1_extractor
        if format == "html" and extractor != "none":
            extracted, out_format, used_fallback = extract_main_content(
                content,
                url,
                extractor=extractor,
                min_content_length=self._config.cleaning.min_content_length,
            )
        else:
            extracted, out_format, used_fallback = content, format, False

        # Stage 2: HTML → Markdown conversion
        converter = pcfg.stage2_converter
        markdown = convert_to_markdown(extracted, converter=converter)

        # Stage 3: Markdown cleaning
        if pcfg.stage3_clean:
            markdown = clean_markdown(
                markdown,
                extra_patterns=self._config.cleaning.additional_boilerplate_patterns,
            )

        # Stage 4: Deduplication
        content_hash: str | None = None
        content_unchanged = False
        if pcfg.stage4_deduplicate and self._dedup is not None:
            content_hash, content_unchanged = await self._dedup.check(url, markdown)

        processed_len = len(markdown)
        reduction_pct = (
            round((1 - processed_len / raw_len) * 100, 1)
            if raw_len > 0 else 0.0
        )

        return PostProcessingResult(
            content=markdown,
            format="markdown",
            extractor_used=extractor if extractor != "none" else None,
            extraction_fallback=used_fallback,
            content_length_raw=raw_len,
            content_length_processed=processed_len,
            reduction_pct=reduction_pct,
            content_unchanged=content_unchanged,
            content_hash=content_hash,
        )
```

- [ ] **Step 2: Verify**

Run: `source .venv/bin/activate && python -c "from serp_llm.post_processing.pipeline import PostProcessingPipeline, PostProcessingResult; print('OK')"`
Expected: prints OK

- [ ] **Step 3: Commit**

```bash
git add src/serp_llm/post_processing/pipeline.py && git commit -m "feat: add PostProcessingPipeline orchestrator"
```

---

### Task 7: Schema additions + AuditEntry additions

**Files:**
- Modify: `src/serp_llm/schemas.py`
- Modify: `src/serp_llm/audit.py`

- [ ] **Step 1: Add PostProcessingInfo + override schema to schemas.py**

Append to `src/serp_llm/schemas.py`:

```python
# ---------------------------------------------------------------------------
# Post-processing pipeline metadata
# ---------------------------------------------------------------------------


class PostProcessingOverride(BaseModel):
    """Request-level override for the post-processing pipeline."""
    skip: bool = False


class PostProcessingInfo(BaseModel):
    extractor_used: str | None = None
    extraction_fallback: bool = False
    content_length_raw: int = 0
    content_length_processed: int = 0
    reduction_pct: float = 0.0
    content_unchanged: bool = False
    content_hash: str | None = None
```

Add `post_processing` field to `ExtractRequest`:

```python
class ExtractRequest(BaseModel):
    url: str
    format: str = "markdown"
    provider: str | None = None
    policy_override: dict | None = None
    wait_for_selector: str | None = None
    session_profile: str | None = None
    cache: CacheControl | None = None
    post_processing: PostProcessingOverride | None = None
```

Add `post_processing` field to `ExtractResponse`:

```python
class ExtractResponse(BaseModel):
    content: str
    format: str = "markdown"
    url: str
    provider_used: str
    request_id: str
    latency_ms: int
    cached: bool = False
    cache_age_seconds: int | None = None
    quality_warning: bool = False
    post_processing: PostProcessingInfo | None = None
```

- [ ] **Step 2: Add audit fields to AuditEntry**

Extend `AuditEntry` in `audit.py`:

```python
    extractor_used: str | None = None
    extraction_fallback: bool = False
    content_length_raw: int = 0
    content_length_processed: int = 0
    content_unchanged: bool = False
```

- [ ] **Step 3: Verify**

Run: `source .venv/bin/activate && python -c "
from serp_llm.schemas import ExtractResponse, PostProcessingInfo
pp = PostProcessingInfo(extractor_used='trafilatura', reduction_pct=92.5)
resp = ExtractResponse(content='test', url='x', provider_used='p', request_id='r', latency_ms=10, post_processing=pp)
print('OK:', resp.post_processing.extractor_used)
"`
Expected: prints "OK: trafilatura"

- [ ] **Step 4: Commit**

```bash
git add src/serp_llm/schemas.py src/serp_llm/audit.py && git commit -m "feat: add post_processing fields to ExtractResponse and AuditEntry"
```

---

### Task 8: GatewayService integration

**Files:**
- Modify: `src/serp_llm/service.py`

- [ ] **Step 1: Add pipeline to GatewayService**

Add import:

```python
from serp_llm.post_processing.pipeline import PostProcessingPipeline, PostProcessingResult
```

Add `post_processing` parameter to `__init__`:

```python
        post_processing: PostProcessingPipeline | None = None,
```

Add assignment:

```python
        self._post_processing = post_processing
```

- [ ] **Step 2: Add pipeline execution to extract()**

In the `extract()` method, after the login wall detection block (around line ~562), BEFORE `dlp_in_count = 0`, add:

```python
        # --- post-processing pipeline ---
        pp_info: PostProcessingInfo | None = None
        if (
            self._post_processing is not None
            and request.format != "html"
            and not (request.post_processing and getattr(request.post_processing, "skip", False))
        ):
            pp_result = await self._post_processing.run(
                content=result.content,
                url=request.url,
                format=result.format,
                provider=provider_used,
            )
            result.content = pp_result.content
            result.format = pp_result.format
            pp_info = PostProcessingInfo(
                extractor_used=pp_result.extractor_used,
                extraction_fallback=pp_result.extraction_fallback,
                content_length_raw=pp_result.content_length_raw,
                content_length_processed=pp_result.content_length_processed,
                reduction_pct=pp_result.reduction_pct,
                content_unchanged=pp_result.content_unchanged,
                content_hash=pp_result.content_hash,
            )
```

Make sure to import `PostProcessingInfo` from schemas.

**Important:** The post-processing runs BEFORE the quality validator, so the quality check runs on processed (cleaned) content. This means the length check in the quality validator now checks cleaned content length, not raw HTML length.

- [ ] **Step 3: Add pp_info to ExtractResponse construction**

In the `ExtractResponse` construction (around line ~533-542), add:

```python
            post_processing=pp_info,
```

- [ ] **Step 4: Add extraction fields to success audit log**

In the success AuditEntry construction, add:

```python
                extractor_used=pp_result.extractor_used if pp_info else None,
                extraction_fallback=pp_result.extraction_fallback if pp_info else False,
                content_length_raw=pp_result.content_length_raw if pp_info else 0,
                content_length_processed=pp_result.content_length_processed if pp_info else 0,
                content_unchanged=pp_result.content_unchanged if pp_info else False,
```

(replace `pp_result` references with actual variables from the pipeline section)

- [ ] **Step 5: Verify**

Run: `source .venv/bin/activate && python -c "
from serp_llm.main import app
print('App loaded:', app.title)
"`
Expected: prints "App loaded: serpLLM"

- [ ] **Step 6: Commit**

```bash
git add src/serp_llm/service.py && git commit -m "feat: integrate post-processing pipeline into GatewayService"
```

---

### Task 9: Wire pipeline in main.py + config.yaml

**Files:**
- Modify: `src/serp_llm/main.py`
- Modify: `config.yaml`

- [ ] **Step 1: Add pipeline initialization to main.py lifespan**

Add imports:

```python
from serp_llm.post_processing.dedup import DedupStore
from serp_llm.post_processing.pipeline import PostProcessingPipeline
```

After `resource_manager` initialization (or near the end of lifespan setup), add:

```python
    # --- Post-processing pipeline ---
    dedup_store = None
    pp_config = config_manager.config.post_processing
    if pp_config.deduplication.enabled:
        dedup_store = DedupStore(db_path="data/dedup.db")
    post_processing = PostProcessingPipeline(
        config=pp_config,
        dedup_store=dedup_store,
    )
    app.state.dedup_store = dedup_store
    app.state.post_processing = post_processing
```

Update `GatewayService` constructor call:

```python
    gateway_service = GatewayService(
        config_manager,
        policy_engine,
        provider_registry,
        proxy_resolver,
        audit_logger,
        cache_store=cache_store,
        dlp_middleware=dlp_middleware,
        resource_manager=resource_manager,
        session_manager=session_manager,
        post_processing=post_processing,
    )
```

- [ ] **Step 2: Add config.yaml section**

Add after `sessions:` or `stealth:` section:

```yaml
# ---------------------------------------------------------------------------
# Content Post-Processing — cleans raw HTML/markdown from scrape providers
# ---------------------------------------------------------------------------
post_processing:
  default:
    stage1_extractor: trafilatura
    stage2_converter: markdownify
    stage3_clean: true
    stage4_deduplicate: false

  cleaning:
    min_content_length: 200
    additional_boilerplate_patterns: []

  deduplication:
    enabled: false
    store: sqlite

  providers:
    firecrawl:
      stage1_extractor: none
      stage2_converter: none
      stage3_clean: true
    jina:
      stage1_extractor: none
      stage2_converter: none
      stage3_clean: true
```

- [ ] **Step 3: Verify**

Run: `source .venv/bin/activate && python -c "
from serp_llm.main import app
print('App loaded:', app.title)
" && source .venv/bin/activate && pytest tests/unit/ -v --tb=short 2>&1 | tail -5`
Expected: 153 passed

- [ ] **Step 4: Commit**

```bash
git add src/serp_llm/main.py config.yaml && git commit -m "feat: wire post-processing pipeline into app lifespan"
```

---

### Task 10: Unit tests

**Files:**
- Create: `tests/unit/test_post_processing.py`

- [ ] **Step 1: Create comprehensive test file**

```python
from __future__ import annotations

import tempfile

import pytest

from serp_llm.post_processing.cleaners import clean_markdown
from serp_llm.post_processing.converters import convert_to_markdown
from serp_llm.post_processing.dedup import DedupStore
from serp_llm.post_processing.extractors import extract_main_content, readability_extract, trafilatura_extract


SAMPLE_HTML = """
<html><body>
<nav>Navigation links</nav>
<article><h1>Article Title</h1>
<p>This is the main article content with enough text to pass the minimum length check for extraction testing purposes.</p>
</article>
<footer>Footer content</footer>
</body></html>
"""


class TestExtractors:
    def test_trafilatura_extracts_content(self):
        """trafilatura should extract the article content from HTML."""
        result = trafilatura_extract(SAMPLE_HTML, "https://example.com/article")
        assert result is not None
        assert "Article Title" in result
        assert len(result) > 50

    def test_readability_extracts_content(self):
        """readability-lxml should extract the article content."""
        result = readability_extract(SAMPLE_HTML)
        assert result is not None
        assert "Article Title" in result

    def test_extract_main_content_with_trafilatura(self):
        content, fmt, fallback = extract_main_content(
            SAMPLE_HTML, "https://example.com/article",
            extractor="trafilatura",
        )
        assert fmt == "markdown"
        assert not fallback
        assert "Article Title" in content

    def test_extract_main_content_none_returns_raw(self):
        content, fmt, fallback = extract_main_content(
            "<html><body>raw</body></html>",
            "https://example.com",
            extractor="none",
        )
        assert fmt == "html"
        assert not fallback
        assert "raw" in content

    def test_extract_main_content_empty_falls_back(self):
        content, fmt, fallback = extract_main_content(
            "<html><body></body></html>",
            "https://example.com",
            extractor="trafilatura",
            min_content_length=1000,  # impossible to reach
        )
        assert fmt == "html"
        assert fallback  # should have fallen back to readability then raw


class TestConverters:
    def test_markdownify_converts_html(self):
        result = convert_to_markdown("<h1>Title</h1><p>Paragraph</p>", converter="markdownify")
        assert "# Title" in result
        assert "Paragraph" in result

    def test_convert_skip_for_markdown(self):
        result = convert_to_markdown("Already **markdown**", converter="markdownify")
        assert result == "Already **markdown**"

    def test_convert_none_returns_original(self):
        result = convert_to_markdown("<p>test</p>", converter="none")
        assert result == "<p>test</p>"


class TestCleaners:
    def test_collapse_whitespace(self):
        result = clean_markdown("a\n\n\n\nb")
        assert "a\n\nb" in result

    def test_remove_boilerplate(self):
        result = clean_markdown("Content\nCookie Policy\nMore")
        assert "Cookie Policy" not in result
        assert "More" in result

    def test_extra_patterns(self):
        result = clean_markdown(
            "Content\nRead more: example.com\nEnd",
            extra_patterns=[r"(?i)^Read more:.*$"],
        )
        assert "Read more:" not in result
        assert "End" in result

    def test_empty_lines_stripped(self):
        result = clean_markdown("a\n   \nb")
        lines = result.split("\n")
        assert not any(line.strip() == "" and line != "" for line in lines)


class TestDedupStore:
    @pytest.fixture
    def store(self):
        s = DedupStore(tempfile.mktemp(suffix=".db"))
        yield s
        s.close()

    async def test_first_seen_not_unchanged(self, store: DedupStore):
        _, unchanged = await store.check("https://example.com", "hello")
        assert not unchanged

    async def test_same_content_unchanged(self, store: DedupStore):
        url = "https://example.com"
        await store.check(url, "hello")
        _, unchanged = await store.check(url, "hello")
        assert unchanged

    async def test_different_content_not_unchanged(self, store: DedupStore):
        url = "https://example.com"
        await store.check(url, "hello")
        _, unchanged = await store.check(url, "world")
        assert not unchanged

    def test_content_hash_consistency(self):
        h1 = DedupStore.content_hash("hello")
        h2 = DedupStore.content_hash("hello")
        assert h1 == h2
        h3 = DedupStore.content_hash("world")
        assert h1 != h3
```

- [ ] **Step 2: Run tests**

Run: `source .venv/bin/activate && pytest tests/unit/test_post_processing.py -v --tb=short 2>&1`
Expected: all tests pass

- [ ] **Step 3: Run full suite + lint**

Run: `source .venv/bin/activate && pytest tests/unit/ -v --tb=short 2>&1 | tail -5`
`source .venv/bin/activate && ruff check src/serp_llm/ 2>&1`

Expected: all tests pass, lint clean

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_post_processing.py && git commit -m "test: add post-processing pipeline unit tests"
```
