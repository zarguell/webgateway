# Extraction Strategies Design

## Problem

Trafilatura is excellent for articles but loses structure on product pages, listings, and modern JS-rendered sites that embed structured data (JSON-LD, schema.org). Agents get flat markdown when the page contains queryable structured data.

## Approach

Add a **strategy selector** to the post-processing pipeline that picks the best extraction method per domain via policy rules. Strategies produce structured data internally; whether that structure is exposed depends on the requested output format.

### Two-tier output

| Format | MCP default? | REST? | Behavior |
|---|---|---|---|
| `markdown` | ✅ default | ✅ | Structured data flattened to readable text |
| `json` | ❌ | ✅ | Raw structured data preserved in `structured_data` field |

MCP stays pure markdown. REST agents building DB pipelines get structured JSON.

## Architecture

```
Provider returns raw HTML
  → Strategy selector (policy-driven, by domain/URL pattern)
    → Strategy A (json_ld)       ← tries first
    → Strategy B (meta_extract)   ← tries second, if A returned nothing
    → Default: article_extract    ← trafilatura if all strategies returned nothing
  → Output formatter (markdown vs json)
    → strategy produced structured data + format=markdown: flatten to text
    → strategy produced structured data + format=json: preserve in structured_data
    → strategy didn't run/article_extract: current behavior unchanged
  → Rest of pipeline (DLP, cleaning, dedup, injection detect, cache write)
    → Text content always goes through DLP/cleaning regardless of strategy
    → structured_data field bypasses text transformations
```

- Strategy selector is a new step in the post-processing pipeline, running on the raw HTML **before** trafilatura.
- Strategies are tried in priority order. First non-empty extraction wins.
- The selected strategy's output is passed to the output formatter.
- Configurable via policy rules: `extract_strategy: priority` where strategies are listed in priority order.

## MVP: Batch 1 — 2 strategies

### Strategy 1: `json_ld`

Extract all `<script type="application/ld+json">` blocks from the HTML. Parse each as JSON. Select the most relevant block by:
1. Priority of `@type` — `Product`, `Recipe`, `JobPosting`, `Event`, `Article` rank highest
2. Block with the most fields wins tiebreaker

**Output format:**
- Markdown: Render as formatted sections (title, price, rating, description)
- JSON: Return the raw JSON-LD object(s) in `structured_data`

### Strategy 2: `meta_extract`

Extract all `<meta>` tags, `<link>` tags, and Open Graph / Twitter Card properties.

**Output format:**
- Markdown: Render as key-value pairs at the top of the content
- JSON: Return as a flat `{og:title, twitter:card, description, ...}` object

### Default strategy: `article_extract`

Current `trafilatura → markdownify` pipeline. Unchanged.

## Policy Configuration

```yaml
policies:
  - name: amazon-product
    match:
      domain: "*.amazon.com"
    extract_strategy:
      priority:
        - json_ld
        - meta_extract
        - article_extract   # default fallback

  - name: wikipedia
    match:
      domain: "*.wikipedia.org"
    extract_strategy:
      priority:
        - article_extract   # json_ld is sparse on Wikipedia, trafilatura works better
```

If `extract_strategy` is not set in the policy, the pipeline behaves exactly as today (article_extract only).

## Implementation Plan

### Phase 1 (1 session): Strategy selector + json_ld

- `src/webgateway/postprocessing/strategies/__init__.py` — Strategy registry and selector
- `src/webgateway/postprocessing/strategies/json_ld.py` — JSON-LD extractor
- Add `extract_strategy` to policy engine config model
- Wire strategy selector into `service.py` post-processing pipeline
- Unit tests for json_ld strategy
- Add amazon.com policy rule to config with json_ld priority

### Phase 2 (1 session): meta_extract + policy refinement

- `src/webgateway/postprocessing/strategies/meta_extract.py`
- Integration test with real amazon.com page (via CDP Chrome)
- Add more domain policy rules (wikipedia, imdb, etc.)
- Unit tests for meta_extract

### Phase 3 (future): next_data, schema_markup, more domains

## Files Changed

| File | Action |
|---|---|
| `src/webgateway/postprocessing/strategies/__init__.py` | **Create** — registry + selector |
| `src/webgateway/postprocessing/strategies/json_ld.py` | **Create** — JSON-LD extractor |
| `src/webgateway/postprocessing/strategies/meta_extract.py` | **Create** — meta tag extractor |
| `src/webgateway/postprocessing/service.py` | **Modify** — wire strategy selector into pipeline |
| `src/webgateway/config.py` | **Modify** — extract_strategy in policy config model |
| `src/webgateway/schemas.py` | **Modify** — add `structured_data` field to ExtractResponse |
| `config.yaml` | **Modify** — add per-domain policy rules |
| `config.local.yaml` | **Modify** — add per-domain policy rules |
| `tests/unit/test_strategies.py` | **Create** — unit tests |

## Non-goals

- No new MCP tools or tool parameters
- No changes to the `web_extract` MCP tool signature
- No browser-side strategy detection (server-side only)
- No LLM-based strategy selection (deterministic priority only)
- No changes to existing article_extract behavior
