# serpLLM PRD Addendum v0.6 — Content Post-Processing Pipeline

**Date:** 2026-06-17
**Supplements:** PRD v0.1 + Addenda v0.3, v0.4, v0.5
**Status:** Pre-development

***

## Section 26 — Content Post-Processing Pipeline

### 26.1 Purpose

Raw HTML from any browser or scrape provider is not LLM-friendly. It contains navigation bars, cookie banners, footers, ads, and boilerplate that contribute nothing to agent reasoning but consume significant context tokens. A post-processing pipeline runs between provider response and response normalization — every scrape result passes through it before being returned to the agent or written to cache.

This pipeline is **provider-agnostic** — it runs on HTML output from invisible_playwright, Camoufox, Firecrawl, Crawl4AI, Jina, or any other scrape provider that returns HTML. Providers that already return markdown (Jina, Firecrawl) can skip the extraction stage but still benefit from the cleaning and deduplication stages.

### 26.2 Pipeline Stages

```
Raw HTML (from any provider)
      ↓
Stage 1: Main Content Extraction     (trafilatura / readability-lxml)
      ↓
Stage 2: HTML → Markdown Conversion  (markdownify / html2text)
      ↓
Stage 3: Markdown Cleaning           (strip residual noise, normalize whitespace)
      ↓
Stage 4: Deduplication Check         (SHA-256 chunk hash, optional)
      ↓
Normalized ScrapeResponse { content, format: "markdown", ... }
```

### 26.3 Stage 1 — Main Content Extraction

This is the highest-value stage. A good content extractor removes 60–80% of token waste from navigation, ads, and boilerplate before any markdown conversion occurs. Raw HTML → markdown without this step retains all noise.

**Primary: trafilatura**

```python
import trafilatura

def extract_main_content(html: str, url: str) -> str:
    result = trafilatura.extract(
        html,
        url=url,
        output_format="markdown",
        include_links=True,
        include_images=False,    # configurable
        include_tables=True,
        no_fallback=False        # fall back to full-page if extraction fails
    )
    return result or html        # if extraction returns None, pass raw HTML downstream
```

trafilatura strips nav, ads, footers, and cookie banners automatically, retains semantic structure (headings, lists, code blocks), and outputs markdown natively. It is the top choice for article and documentation extraction.

**Fallback: readability-lxml**

When trafilatura returns None or very short content (extraction failed), fall back to readability-lxml which produces clean HTML, then pass to Stage 2 for markdown conversion:

```python
from readability import Document

def extract_with_readability(html: str) -> str:
    doc = Document(html)
    return doc.summary()    # returns clean HTML of main content
```

**Extraction failure handling:**
If both extractors return content below a minimum length threshold (configurable, default 200 chars), the pipeline passes the full raw HTML to Stage 2 rather than returning empty content. This is logged as `extraction_fallback: true` in the audit log.

### 26.4 Stage 2 — HTML → Markdown Conversion

Only runs if Stage 1 did not already return markdown (trafilatura with `output_format="markdown"` covers most cases). Handles the readability-lxml fallback path and any provider returning raw HTML:

**Primary: markdownify**

```python
from markdownify import markdownify as md

def html_to_markdown(html: str) -> str:
    return md(
        html,
        heading_style="ATX",        # ## style headings
        bullets="-",
        strip=["script", "style", "nav", "footer", "header"]
    )
```

**Alternative: html2text**
Faster, simpler, no boilerplate removal — use when markdownify is unavailable or for simple pages where trafilatura already handled extraction:

```python
import html2text
h = html2text.HTML2Text()
h.ignore_links = False
h.ignore_images = True
markdown = h.handle(html)
```

### 26.5 Stage 3 — Markdown Cleaning

Light normalization pass on the markdown output regardless of which path produced it:

```python
import re

def clean_markdown(md: str) -> str:
    # collapse 3+ blank lines to 2
    md = re.sub(r'\n{3,}', '\n\n', md)
    # strip lines that are only whitespace
    md = '\n'.join(line for line in md.splitlines() if line.strip() or line == '')
    # remove common residual boilerplate patterns
    boilerplate_patterns = [
        r'(?i)^(cookie policy|accept cookies|privacy policy)\s*$',
        r'(?i)^(subscribe to (our )?newsletter)\s*$',
        r'(?i)^(share this article|share on)\s*.*$',
    ]
    for pattern in boilerplate_patterns:
        md = re.sub(pattern, '', md, flags=re.MULTILINE)
    return md.strip()
```

Boilerplate patterns are configurable in `config.yaml` — operators can add site-specific patterns:

```yaml
post_processing:
  cleaning:
    additional_boilerplate_patterns:
      - "(?i)^Read more:.*$"
      - "(?i)^Filed under:.*$"
```

### 26.6 Stage 4 — Deduplication (Optional)

SHA-256 hash of the cleaned markdown content. Compared against a deduplication store (SQLite table) to detect unchanged content on re-fetch:

```python
import hashlib

def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()
```

```sql
CREATE TABLE content_hashes (
  url_hash      TEXT PRIMARY KEY,    -- SHA-256 of URL
  content_hash  TEXT NOT NULL,       -- SHA-256 of cleaned markdown
  ts            DATETIME NOT NULL
);
```

If the content hash matches the stored hash for that URL, the response includes `content_unchanged: true`. The agent can use this signal to skip re-processing or re-embedding. This does not affect cache behavior — it is a content-level signal, not a cache control mechanism.

```yaml
post_processing:
  deduplication:
    enabled: false          # opt-in, off by default
    store: sqlite
```

### 26.7 Per-Provider Pipeline Configuration

Different providers need different pipeline treatment. Configured per provider:

```yaml
post_processing:
  providers:
    invisible_playwright:
      stage1_extractor: trafilatura    # full pipeline
      stage2_converter: markdownify
      stage3_clean: true
      stage4_deduplicate: false

    firecrawl:
      stage1_extractor: none           # already returns clean markdown
      stage2_converter: none
      stage3_clean: true               # still benefit from whitespace normalization
      stage4_deduplicate: false

    jina:
      stage1_extractor: none           # already returns markdown
      stage2_converter: none
      stage3_clean: true
      stage4_deduplicate: false

    crawl4ai:
      stage1_extractor: trafilatura
      stage2_converter: markdownify
      stage3_clean: true
      stage4_deduplicate: false

  default:
    stage1_extractor: trafilatura
    stage2_converter: markdownify
    stage3_clean: true
    stage4_deduplicate: false
```

### 26.8 Request-Level Override

Agents can request raw output (no post-processing) for cases where they need the full HTML or unprocessed markdown:

```json
POST /scrape
{
  "url": "https://...",
  "format": "html",              // skip all post-processing, return raw HTML
  "post_processing": {
    "skip": true                 // explicit override
  }
}
```

Supported `format` values:
- `markdown` (default) — full pipeline runs
- `html` — raw HTML returned, no pipeline
- `text` — pipeline runs, output is plain text (trafilatura `output_format="text"`)

### 26.9 Extractor Tool Reference

| Tool | Output | Best For | Boilerplate Removal |
|---|---|---|---|
| trafilatura | Markdown / text | Articles, docs, news | ✅ Automatic |
| readability-lxml | Clean HTML | Main content fallback | ✅ Automatic |
| markdownify | Markdown | Full-fidelity HTML→MD | ❌ Manual patterns |
| html2text | Markdown | Simple, fast conversion | ❌ Minimal |

All four are pure Python, pip-installable, zero system dependencies — baked into the gateway image.

### 26.10 browser-use Integration Note

`browser-use` is an agent framework wrapping Playwright with a built-in `get_page_as_markdown` action. invisible_playwright can be used as its underlying engine. This is **not** part of the gateway's core pipeline — it is noted here as an alternative pattern for operators building agents that want more automated browser interaction with built-in extraction. The gateway's post-processing pipeline is preferable for server-side normalization because it is provider-agnostic and centrally configurable.

### 26.11 Normalized Response Fields (additions to PRD Section 4.5)

```json
{
  "content": "...",
  "format": "markdown",
  "post_processing": {
    "extractor_used": "trafilatura",
    "extraction_fallback": false,
    "content_length_raw": 84200,
    "content_length_processed": 3100,
    "reduction_pct": 96,
    "content_unchanged": false,
    "content_hash": "a3f9c2..."
  }
}
```

`reduction_pct` is particularly useful for debugging — a 96% reduction means trafilatura stripped nearly all the page and retained only the article body. A 10% reduction on a known article page suggests extraction failed and the full HTML came through — worth investigating.

### 26.12 Audit Log Fields (additions to PRD Section 4.7)

```json
{
  "extractor_used": "trafilatura",
  "extraction_fallback": false,
  "content_length_raw": 84200,
  "content_length_processed": 3100,
  "content_unchanged": false
}
```

***

## Updated Pipeline Position

Post-processing sits between provider response and the existing content quality validator — extraction must happen before the quality validator runs its length check, since raw HTML will always pass a length check even when the actual content is empty:

```
Provider response (raw HTML or markdown)
      ↓
Post-Processing Pipeline          ← NEW (Section 26)
  Stage 1: Content extraction
  Stage 2: HTML → Markdown
  Stage 3: Markdown cleaning
  Stage 4: Deduplication check
      ↓
Content Quality Validator         ← existing (Section 17.6)
  Length check (now on processed content, not raw HTML)
  JS blob check (catches extraction failures)
  Login wall check
      ↓
Response Normalizer
      ↓
Cache write
      ↓
Audit logger
      ↓
Agent
```

***

## Config Schema Additions (supplements all prior addenda)

```yaml
post_processing:
  default:
    stage1_extractor: trafilatura      # trafilatura | readability | none
    stage2_converter: markdownify      # markdownify | html2text | none
    stage3_clean: true
    stage4_deduplicate: false

  cleaning:
    min_content_length: 200            # below this, fall back to raw HTML
    additional_boilerplate_patterns: []

  deduplication:
    enabled: false
    store: sqlite

  providers:                           # per-provider overrides
    firecrawl:
      stage1_extractor: none
      stage2_converter: none
      stage3_clean: true
    jina:
      stage1_extractor: none
      stage2_converter: none
      stage3_clean: true
```

***

## Build Order Additions (appends to Addendum v0.5 order)

- **41.** trafilatura + readability-lxml + markdownify + html2text added to gateway image dependencies
- **42.** Stage 1 — trafilatura extractor with readability fallback
- **43.** Stage 2 — markdownify converter (html2text as fallback)
- **44.** Stage 3 — markdown cleaning pass + configurable boilerplate patterns
- **45.** Stage 4 — SHA-256 deduplication store (opt-in, SQLite)
- **46.** Per-provider pipeline config + request-level `post_processing.skip` override
- **47.** `post_processing` fields in normalized response and audit log
- **48.** Content quality validator length check updated to run on processed content

