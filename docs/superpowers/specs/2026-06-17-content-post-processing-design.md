# Content Post-Processing Pipeline Design

**Date:** 2026-06-17
**Status:** Draft
**Source:** PRD Addendum v0.6 (§26)

---

## 1. Scope

Add a provider-agnostic 4-stage content post-processing pipeline that cleans raw HTML/markdown from scrape providers before it reaches the agent.

**In scope:**
- Stage 1: Main content extraction (trafilatura, with readability-lxml fallback)
- Stage 2: HTML → Markdown conversion (markdownify primary, html2text fallback)
- Stage 3: Markdown cleaning (whitespace normalization + configurable boilerplate patterns)
- Stage 4: SHA-256 deduplication (opt-in, SQLite store)
- Per-provider pipeline config (which stages run for each provider)
- Request-level `post_processing.skip` override
- `post_processing` fields in ExtractResponse and AuditEntry
- Content quality validator runs on processed content (not raw HTML)
- Pipeline integration into GatewayService.extract()

**Out of scope:**
- browser-use integration (noted in PRD as alternative pattern, not gateway core)
- Non-HTML extractors (PDF, image OCR)

---

## 2. Architecture

New module `src/webgateway/post_processing/` with one class per stage concern:

```
GatewayService.extract()
  ↓ provider response (raw HTML or markdown)
  ↓
PostProcessingPipeline.run()
  → resolves per-provider config
  → Stage 1: extractor.extract(html, url) → html or markdown
  → Stage 2: converter.convert(html) → markdown (only if stage 1 returned html)
  → Stage 3: cleaner.clean(markdown) → cleaned markdown
  → Stage 4: dedup.check(url, content) → (content, content_unchanged flag)
  → PostProcessingResult { content, format, extractor_used, fallback, stats }
  ↓
Content Quality Validator (now validates processed content)
  ↓
Response Normalizer
```

### Module layout

```
src/webgateway/post_processing/
  __init__.py
  pipeline.py     # PostProcessingPipeline — orchestrates stages
  extractors.py   # trafilatura + readability wrappers
  converters.py   # markdownify + html2text wrappers
  cleaners.py     # markdown cleaning, boilerplate removal
  dedup.py        # SHA-256 deduplication store
```

---

## 3. Config Model

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

---

## 4. Pipeline Stages

### Stage 1 — Main Content Extraction

```python
def extract_main_content(html: str, url: str, extractor: str) -> str:
    """Run the configured extractor on raw HTML. Returns markdown or HTML."""
```

- `trafilatura` — primary, outputs markdown natively, strips nav/ads/footers
- `readability` — fallback, outputs clean HTML (passed to Stage 2 for markdown conversion)
- `none` — skip extraction, pass raw HTML to Stage 2
- If both extractors fail (< `min_content_length` chars), log `extraction_fallback: true` and pass raw HTML

### Stage 2 — HTML → Markdown

```python
def convert_to_markdown(html: str, converter: str) -> str:
    """Convert HTML to markdown. Only runs if input is HTML (not already markdown)."""
```

- Auto-detects if input is already markdown (no HTML tags) — skips if so
- `markdownify` — primary, strips script/style/nav/footer/header
- `html2text` — fallback, simpler/faster

### Stage 3 — Markdown Cleaning

```python
def clean_markdown(md: str, extra_patterns: list[str]) -> str:
    """Normalize whitespace, strip residual boilerplate."""
```

- Collapse 3+ blank lines to 2
- Strip whitespace-only lines
- Remove configurable boilerplate patterns (cookie notices, subscribe prompts, etc.)
- Strip extra patterns from `config.post_processing.cleaning.additional_boilerplate_patterns`

### Stage 4 — Deduplication

```python
class DedupStore:
    def __init__(self, db_path: str): ...
    async def check(self, url: str, content: str) -> tuple[str, bool]:
        """Return (content, content_unchanged). Updates hash if changed."""
```

- SHA-256 hash of cleaned markdown
- SQLite table `content_hashes(url_hash, content_hash, ts)`
- Opt-in, off by default
- Returns `content_unchanged: bool` signal for the agent

---

## 5. Service Integration

In `GatewayService.extract()`, after `_execute_with_fallback()` returns (and after login wall check), add:

```python
# --- post-processing pipeline ---
if (
    self._post_processing is not None
    and request.format != "html"
    and not (request.post_processing and request.post_processing.skip)
):
    pp_result = await self._post_processing.run(
        content=result.content,
        url=request.url,
        format=result.format,
        provider=provider_used,
    )
    result.content = pp_result.content
    result.format = "markdown"
    pp_info = pp_result.to_info()
else:
    pp_info = None
```

The quality validator then runs on `result.content` (which is now processed, not raw HTML).

---

## 6. Files Changed / Created

| File | Action |
|------|--------|
| `src/webgateway/post_processing/__init__.py` | CREATE |
| `src/webgateway/post_processing/pipeline.py` | CREATE |
| `src/webgateway/post_processing/extractors.py` | CREATE |
| `src/webgateway/post_processing/converters.py` | CREATE |
| `src/webgateway/post_processing/cleaners.py` | CREATE |
| `src/webgateway/post_processing/dedup.py` | CREATE |
| `src/webgateway/config.py` | MODIFY — PostProcessingConfig |
| `src/webgateway/schemas.py` | MODIFY — PostProcessingInfo |
| `src/webgateway/service.py` | MODIFY — pipeline integration |
| `src/webgateway/audit.py` | MODIFY — extraction audit fields |
| `src/webgateway/main.py` | MODIFY — wire pipeline |
| `config.yaml` | MODIFY — post_processing section |
| `tests/unit/` | MODIFY — pipeline unit tests |
| `pyproject.toml` | MODIFY — add deps |
