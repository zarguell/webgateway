# LLM Judge — Tier 2 Routing Design

**Date:** 2026-06-18
**Status:** Approved
**Implements:** PRD §10 (LLM Judge), PRD §14 build order step 10
**References:** PRD-addendum §17.6 (quality failure feedback), §18.6 (quota-aware routing), §19.3 (docs routing)

---

## 1. Purpose

The LLM Judge is Tier 2 of the policy engine. When Tier 1 deterministic YAML rules produce no match (policy miss), or when a provider fails during dispatch (retry/error), the judge calls a local LLM to make a structured routing decision. It returns a provider selection, a fallback suggestion, a reasoning tag for audit, and a confidence score. Below the configured confidence threshold, the judge defers to defaults without retrying.

**Environment:** LM Studio running locally at `http://127.0.0.1:1234` with OpenAI-compatible API. Model: `google/gemma-4-e2b`.

---

## 2. Architecture Decision

**Judge as `GatewayService` collaborator** — not inside `PolicyEngine`.

Rationale:
- `PolicyEngine` is synchronous and pure (YAML rule evaluation only). The judge is async I/O (HTTP call to LLM). Mixing them would break the sync contract or require sync-over-async hacks.
- `GatewayService` already orchestrates async collaborators: `CacheStore`, `DlpMiddleware`, `ProviderResourceManager`, `SessionManager`. The judge joins them as another collaborator.
- The judge needs access to `ProviderRegistry` (available providers, health, specializations) and error feedback from the dispatch loop — both available in the service layer.

The judge fires at two pipeline stages:

```
Request
  → PolicyEngine.evaluate()                     [Tier 1: sync YAML rules]
  → [policy_matched is None?] → JUDGE: on_policy_miss    [Tier 2: async LLM]
  → DLP outbound
  → Cache lookup
  → _execute_with_fallback()                    [provider dispatch]
      → [provider fails?]
      → PolicyEngine.evaluate_for_error()       [Tier 1: sync error rules]
      → [no error rule match?] → JUDGE: on_retry / on_error_class  [Tier 2]
      → [judge decision?] → redirect to judge provider
      → [no decision?] → continue fallback chain
  → DLP inbound
  → Cache write
  → Response
```

---

## 3. Config Schema Changes

### 3.1 Refactor `LLMJudgeConfig` (breaking change — no existing production configs)

**File:** `src/serp_llm/config.py`, lines 111–118

**Before (Ollama-specific):**
```python
class LLMJudgeConfig(BaseModel):
    enabled: bool = False
    model: str = "ollama/gemma3:1b"
    ollama_url: str = "http://ollama:11434"
    triggers: list[str] = Field(default_factory=list)
    cache_decisions: bool = True
    cache_ttl_seconds: int = 3600
    confidence_threshold: float = 0.70
```

**After (generic OpenAI-compatible):**
```python
class LLMJudgeConfig(BaseModel):
    enabled: bool = False
    model: str = "google/gemma-4-e2b"
    base_url: str = "http://127.0.0.1:1234/v1"
    api_key: str = "lm-studio"
    # Triggers
    trigger_on_policy_miss: bool = True
    trigger_on_retry: bool = True
    trigger_on_error_class: list[str] = Field(
        default_factory=lambda: ["403", "429", "bot_detected", "timeout"]
    )
    # Cache
    cache_decisions: bool = True
    cache_ttl_seconds: int = 3600
    # Quality
    confidence_threshold: float = 0.70
    # LLM call
    timeout: int = 10
    temperature: float = 0.0
```

**Field semantics:**
- `base_url` — OpenAI-compatible API base URL (LM Studio: `http://127.0.0.1:1234/v1`)
- `api_key` — Bearer token. LM Studio accepts any non-empty string.
- `trigger_on_policy_miss` — Judge fires when `PolicyEngine.evaluate()` returns `policy_matched=None`.
- `trigger_on_retry` — Judge fires on any provider failure during fallback dispatch.
- `trigger_on_error_class` — Judge fires only when the error class matches one of these values. Acts as a filter on `trigger_on_retry`. If `trigger_on_retry` is `False` but `trigger_on_error_class` is populated, the judge still fires for matching error classes.
- `temperature` — `0.0` for deterministic routing decisions.
- `timeout` — LLM API call timeout in seconds. Separate from provider timeouts.

### 3.2 Config YAML example

```yaml
llm_judge:
  enabled: true
  model: google/gemma-4-e2b
  base_url: http://127.0.0.1:1234/v1
  api_key: lm-studio
  trigger_on_policy_miss: true
  trigger_on_retry: true
  trigger_on_error_class: ["403", "429", "bot_detected", "timeout"]
  cache_decisions: true
  cache_ttl_seconds: 3600
  confidence_threshold: 0.70
  timeout: 10
  temperature: 0.0
```

### 3.3 `config.test.yaml` — judge disabled

Judge must be disabled in test config to avoid requiring LM Studio in CI:

```yaml
llm_judge:
  enabled: false
```

---

## 4. Judge Module: `src/serp_llm/judge.py`

Single-file module. Four components:

### 4.1 `DecisionCache`

In-memory TTL cache for routing decisions. Not persisted — routing decisions are ephemeral and the cost of a cache miss is one LLM call (~50–500ms), not a provider API call.

```python
class DecisionCache:
    def __init__(self, ttl_seconds: int = 3600) -> None: ...
    def get(self, key: str) -> RoutingDecision | None: ...
    def set(self, key: str, decision: RoutingDecision) -> None: ...
    def clear(self) -> None: ...
```

**Cache key derivation:** `sha256(trigger_type + content_type + url + query + serialized_failed_providers)`. Prior error context is part of the key — same URL with different prior failures produces different cache lookups.

**TTL:** Configurable via `cache_ttl_seconds` (default 3600s). Expired entries are lazily evicted on `get()`.

**Thread safety:** Protected by `threading.Lock` — the cache is read from async code but the lock guards the dict mutation.

### 4.2 `JudgeContext` (dataclass)

Input bundle passed to the prompt builder:

```python
@dataclass
class JudgeContext:
    trigger_type: str          # "on_policy_miss" | "on_retry" | "on_error_class"
    content_type: str          # "search" | "extract"
    url: str | None            # None for search requests
    query: str | None          # None for extract requests
    failed_providers: list[FailedProvider]  # Empty for on_policy_miss
    available_providers: list[ProviderInfo]  # From ProviderRegistry
```

Supporting dataclasses:

```python
@dataclass
class FailedProvider:
    name: str
    error_class: str           # "403", "429", "bot_detected", "timeout", etc.
    message: str               # Error detail from ProviderError

@dataclass
class ProviderInfo:
    name: str
    specialization: str        # "general", "docs", "semantic", etc.
    healthy: bool
    self_hosted: bool
```

### 4.3 `LLMJudge`

Main class. Injected into `GatewayService` constructor.

```python
class LLMJudge:
    def __init__(
        self,
        config_manager: ConfigManager,
        provider_registry: ProviderRegistry,
    ) -> None: ...

    def is_enabled(self) -> bool: ...

    def should_trigger_for_error(self, error_class: str) -> bool: ...

    async def evaluate_policy_miss(
        self,
        content_type: str,
        url: str | None,
        query: str | None,
    ) -> RoutingDecision | None: ...

    async def evaluate_for_retry(
        self,
        content_type: str,
        url: str | None,
        query: str | None,
        failed_providers: list[FailedProvider],
    ) -> RoutingDecision | None: ...
```

**Return contract:**
- Returns `RoutingDecision` with `judge_invoked=True`, `judge_reasoning_tag=<tag>` when the judge makes a confident decision.
- Returns `None` when: judge disabled, trigger not configured, confidence below threshold, LLM call fails, JSON parse fails, cache disabled and decision not cached.
- Caller falls through to default decision / normal fallback chain on `None`.

**Internal flow (both evaluate methods):**
1. Check `is_enabled()` and trigger config → return `None` if not triggered
2. Build `JudgeContext` from inputs + `ProviderRegistry`
3. Compute cache key → check `DecisionCache` → return cached decision if hit
4. Build chat messages via prompt builder
5. Call `_call_llm(messages)` → get response text
6. Parse JSON from response → build `JudgeResponse`
7. Check `confidence >= confidence_threshold` → return `None` if below
8. Build `RoutingDecision` from `JudgeResponse`
9. Store in cache if `cache_decisions` is `True`
10. Return `RoutingDecision`

### 4.4 `_call_llm` — HTTP client

Follows existing provider httpx pattern (per `providers/tavily.py`):

```python
async def _call_llm(self, messages: list[dict]) -> str | None:
    config = self._config_manager.config.llm_judge
    try:
        async with httpx.AsyncClient(timeout=config.timeout) as client:
            resp = await client.post(
                f"{config.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {config.api_key}"},
                json={
                    "model": config.model,
                    "messages": messages,
                    "temperature": config.temperature,
                    "response_format": {"type": "json_object"},
                    "max_tokens": 200,
                },
            )
    except httpx.HTTPError:
        return None  # fail open — fall through to defaults

    if resp.status_code >= 400:
        return None

    data = resp.json()
    return data.get("choices", [{}])[0].get("message", {}).get("content")
```

**Design choices:**
- No retry on the LLM call itself — the judge is an optimization, not critical path. If it fails, defaults apply.
- `max_tokens: 200` — the JSON response is tiny; no need for longer generation.
- `response_format: {"type": "json_object"}` — requests JSON mode. LM Studio ignores this if the model doesn't support it; the JSON parser handles both cases.
- Fresh `httpx.AsyncClient` per call — matches existing provider pattern. No persistent connection needed (judge calls are infrequent — only on policy miss or error).

### 4.5 JSON parsing

The LLM may wrap JSON in markdown fences, add prose, or produce malformed JSON. Parser strategy:

```python
def _parse_judge_response(self, text: str) -> JudgeResponse | None:
    # 1. Strip markdown code fences
    # 2. Extract the first {...} block via regex
    # 3. json.loads()
    # 4. Validate required keys: provider, fallback_if_fail, reasoning_tag, confidence
    # 5. Validate provider is in available_providers list
    # 6. Return JudgeResponse or None on any failure
```

If `provider` in the JSON response is not in the available providers list, the decision is rejected (returns `None`).

### 4.6 `JudgeResponse` (internal dataclass)

```python
@dataclass
class JudgeResponse:
    provider: str
    fallback_if_fail: str | None
    reasoning_tag: str
    confidence: float
```

### 4.7 Building `RoutingDecision` from judge output

```python
def _to_routing_decision(
    self, response: JudgeResponse, content_type: str
) -> RoutingDecision:
    fallback_chain = []
    if response.fallback_if_fail:
        fallback_chain = [response.provider, response.fallback_if_fail]

    return RoutingDecision(
        policy_matched="llm_judge",
        content_type=content_type,
        provider=response.provider,
        fallback_chain=fallback_chain,
        judge_invoked=True,
        judge_reasoning_tag=response.reasoning_tag,
    )
```

**Note:** `proxy`, `dlp_policy`, `allowed_providers`, `playwright_profile` are left at defaults (None). The judge does not override proxy routing or DLP policy — those are Tier 1 concerns.

---

## 5. Prompt Design

### 5.1 System prompt (constant)

```
You are a routing decision engine for a web gateway. Your job is to select the best provider for a web request based on the request context.

Available providers:
{provider_table}

Respond with STRICT JSON only. No prose, no markdown, no explanation.
Output format:
{"provider": "<name>", "fallback_if_fail": "<name or null>", "reasoning_tag": "<snake_case>", "confidence": <0.0-1.0>}

Rules:
- "provider" must be one of the available providers listed above.
- "fallback_if_fail" must be a different provider from the list, or null.
- "reasoning_tag" is a short snake_case label for the routing decision (e.g., "js_heavy_page", "docs_lookup", "anti_bot_retry").
- "confidence" is your confidence in this routing (0.0 to 1.0).

Routing guidelines:
- JS-heavy sites (SPAs, React/Angular/Vue) → firecrawl or crawl4ai
- Documentation sites (MDN, readthedocs, pkg.go.dev) → jina or context7
- Cloudflare/bot-protected sites (403 errors) → firecrawl or crawl4ai
- API docs, versioned library docs → context7
- General web content → jina
- Semantic/similarity search queries → exa
- General search queries → searxng or brave
- Rate-limited providers (429) → switch to a different provider
```

The `{provider_table}` is dynamically generated from `ProviderRegistry`:
```
- jina: general scrape, self-hosted
- firecrawl: general scrape (JS-heavy, anti-bot), self-hosted
- searxng: general search, self-hosted
- brave: general search, managed
- exa: semantic search, managed
- context7: docs search, MCP-native
```

### 5.2 User prompt — on_policy_miss

```
Trigger: on_policy_miss
Content type: {content_type}
URL: {url or "N/A"}
Query: {query or "N/A"}

No policy rule matched this request. Select the best provider.
```

### 5.3 User prompt — on_retry / on_error_class

```
Trigger: {trigger_type}
Content type: {content_type}
URL: {url or "N/A"}
Query: {query or "N/A"}

Failed providers:
1. {name}: {error_class} — {message}
2. {name}: {error_class} — {message}

Select a provider that has not already failed, and is likely to succeed given the error context.
```

---

## 6. Service Layer Integration

### 6.1 Constructor changes (`GatewayService.__init__`)

Add optional `llm_judge: LLMJudge | None = None` parameter. Stored as `self._judge`.

The judge is optional because:
- It's disabled by default in config (`enabled: false`)
- Tests can construct `GatewayService` without a judge
- The service degrades gracefully — if no judge, Tier 1 defaults apply

### 6.2 `main.py` — app factory wiring

In the app factory, construct `LLMJudge` if `config.llm_judge.enabled` is `True`, and inject into `GatewayService`.

### 6.3 Integration point 1: on_policy_miss

**In `search()` (after line ~122) and `extract()` (after line ~360):**

After `PolicyEngine.evaluate()` returns and before DLP outbound check:

```python
decision = self._policy_engine.evaluate(...)

# Tier 2: LLM Judge on policy miss
if (decision.policy_matched is None
        and self._judge is not None
        and self._judge.is_enabled()):
    judged = await self._judge.evaluate_policy_miss(
        content_type="search",  # or "extract"
        url=url,
        query=query,
    )
    if judged is not None:
        decision = judged
```

This is a clean insertion — the `decision` variable is reassigned, and the rest of the pipeline (DLP, cache, dispatch) uses the judge's decision transparently.

### 6.4 Integration point 2: on_retry / on_error_class

**In `_execute_with_fallback()` (lines 745–804):**

The method currently iterates a candidate list and catches `ProviderError`. Changes:

1. Add a `failed_providers: list[FailedProvider]` accumulator.
2. When a provider fails, append `(name, error_class, message)` to `failed_providers`.
3. Determine if the error should trigger the judge:
   - `trigger_on_retry` is `True` → always trigger
   - `error_class` is in `trigger_on_error_class` → trigger
4. Before trying the next candidate, check `PolicyEngine.evaluate_for_error()` first (Tier 1).
5. If no Tier 1 error rule, call `self._judge.evaluate_for_retry(...)` with the accumulated failures.
6. If judge returns a decision:
   - If the judge's provider is already in remaining candidates, move it to position `i+1` (prioritize it). If not, insert it at `i+1` (add as new option). Either way, ensure no duplicate entries in the candidate list.
   - Set `decision.judge_reasoning_tag` for audit
7. If judge returns None, continue normal fallback chain iteration.

**Pseudocode for the modified loop:**

```python
failed_providers = []

for i, candidate in enumerate(candidates):
    try:
        result = await operation(candidate, *args)
        return result, candidate, quality_passed
    except ProviderError as exc:
        failed_providers.append(FailedProvider(
            name=candidate, error_class=exc.error_class or str(exc.status_code),
            message=exc.message
        ))

        # Tier 1: error-based rules
        error_decision = self._policy_engine.evaluate_for_error(
            error_class=failed_providers[-1].error_class,
            content_type=content_type, url=url, query=query,
        )
        if error_decision and error_decision.provider not in [f.name for f in failed_providers]:
            # Redirect to error rule provider — insert as next candidate
            candidates.insert(i + 1, error_decision.provider)
            continue

        # Tier 2: LLM Judge on retry/error
        if self._judge and self._judge.should_trigger_for_error(failed_providers[-1].error_class):
            judged = await self._judge.evaluate_for_retry(
                content_type=content_type, url=url, query=query,
                failed_providers=failed_providers,
            )
            if judged and judged.provider not in [f.name for f in failed_providers]:
                candidates.insert(i + 1, judged.provider)
                continue

        # No redirect — continue to next candidate in chain
```

**Guard:** The judge's suggested provider must not be in `failed_providers`. If it is, the suggestion is rejected and the normal chain continues. This prevents infinite loops.

### 6.5 Context propagation

`_execute_with_fallback()` currently receives `(provider_name, fallback_chain, operation, *args)`. To support the judge, it needs additional context: `content_type`, `url`, `query`. These are passed as a new `RequestContext` parameter:

```python
@dataclass
class RequestContext:
    content_type: str
    url: str | None
    query: str | None
```

Both `search()` and `extract()` build this from their respective inputs and pass it to `_execute_with_fallback()`.

---

## 7. Audit Logging

The existing `AuditEntry` already has `judge_invoked: bool` and `judge_reasoning_tag: str | None` fields (lines 53–54). The `RoutingDecision` already has the same fields (lines 56–57). The `_to_policy_decision()` mapper already passes them through (lines 727–728).

**No changes needed to audit, schemas, or models for field propagation.** The judge sets `judge_invoked=True` and `judge_reasoning_tag=<tag>` on the `RoutingDecision`, and the existing pipeline propagates them to the audit log and API response automatically.

For the retry trigger, the judge's reasoning tag should be recorded in the audit entry. Since `_execute_with_fallback()` is called after the initial `decision` is set, the reasoning tag from a retry-triggered judge call needs to be written back to the `RoutingDecision` so it propagates to the final audit entry. This is handled by updating `decision.judge_reasoning_tag` (and `decision.judge_invoked = True`) inside `_execute_with_fallback()` when the judge fires on retry.

---

## 8. Error Handling and Fail-Open Behavior

The judge **always fails open**. No request should fail because the judge failed.

| Failure mode | Behavior |
|---|---|
| Judge disabled in config | `is_enabled()` returns `False`, never called |
| Trigger not configured | `evaluate_*` returns `None` immediately |
| LLM API unreachable (httpx error) | `_call_llm` returns `None`, `evaluate_*` returns `None` |
| LLM API returns HTTP error (≥400) | Same — returns `None` |
| LLM response not valid JSON | `_parse_judge_response` returns `None` |
| JSON missing required keys | Same — returns `None` |
| Provider in JSON not in available list | Same — returns `None` |
| Confidence below threshold | `evaluate_*` returns `None` |
| Cache miss + all above pass | Decision cached and returned |

In all `None` cases, the caller falls through to default behavior:
- `on_policy_miss`: `RoutingDecision` from `_default_decision()` (existing behavior)
- `on_retry`: next provider in fallback chain (existing behavior)

---

## 9. Testing Strategy

### 9.1 Unit tests: `tests/unit/test_judge.py`

Follow `test_invisible_playwright.py` patterns — use `pytest-httpx` to mock `/v1/chat/completions`.

**Test groups:**

1. **Config tests**
   - `LLMJudgeConfig` defaults are correct
   - Config hot-reload updates judge behavior
   - `is_enabled()` reflects config state

2. **DecisionCache tests**
   - Cache hit returns decision
   - Cache miss returns None
   - TTL expiry evicts entry
   - Different failed_providers produce different cache keys

3. **Prompt builder tests**
   - System prompt includes all available providers
   - on_policy_miss user prompt includes URL/query
   - on_retry user prompt includes failed providers with errors

4. **JSON parser tests**
   - Parses clean JSON
   - Parses JSON wrapped in markdown fences
   - Parses JSON with surrounding prose
   - Returns None for malformed JSON
   - Returns None for missing required keys
   - Returns None for unknown provider

5. **evaluate_policy_miss tests** (mock httpx)
   - Returns RoutingDecision on valid LLM response
   - Returns None when disabled
   - Returns None on LLM API failure
   - Returns None when confidence below threshold
   - Returns cached decision on cache hit (no LLM call)
   - Sets `judge_invoked=True` and `judge_reasoning_tag`

6. **evaluate_for_retry tests** (mock httpx)
   - Returns RoutingDecision with different provider
   - Returns None when trigger not configured
   - Failed providers included in prompt context
   - Judge provider already in failed_providers → returns None (loop prevention)

7. **Integration with GatewayService** (mock LLMJudge with AsyncMock)
   - Judge called on policy miss when enabled
   - Judge not called when disabled
   - Judge decision replaces default decision
   - Judge called on provider failure with error context
   - Judge not called on provider failure when trigger disabled

### 9.2 Integration tests

No live LLM calls in CI. Judge disabled in `config.test.yaml`.

Manual integration test against local LM Studio is possible but not automated:
- Start LM Studio with `google/gemma-4-e2b`
- Enable judge in config
- Make requests that miss policy rules
- Verify audit log shows `judge_invoked: true`

---

## 10. Files Changed

| File | Change |
|---|---|
| `src/serp_llm/judge.py` | **New.** `DecisionCache`, `JudgeContext`, `FailedProvider`, `ProviderInfo`, `JudgeResponse`, `LLMJudge` |
| `src/serp_llm/config.py` | Refactor `LLMJudgeConfig` (lines 111–118): remove `ollama_url`, `triggers`; add `base_url`, `api_key`, `trigger_on_policy_miss`, `trigger_on_retry`, `trigger_on_error_class`, `timeout`, `temperature` |
| `src/serp_llm/service.py` | Add `llm_judge` param to constructor; add judge calls in `search()`, `extract()`, `_execute_with_fallback()`; add `RequestContext` dataclass |
| `src/serp_llm/main.py` | Construct `LLMJudge` when enabled, inject into `GatewayService` |
| `config.test.yaml` | Add `llm_judge: { enabled: false }` |
| `tests/unit/test_judge.py` | **New.** All unit tests from §9.1 |
| `src/serp_llm/policy/models.py` | **No change.** `judge_invoked` and `judge_reasoning_tag` already exist |
| `src/serp_llm/audit.py` | **No change.** Audit fields already exist |
| `src/serp_llm/schemas.py` | **No change.** `PolicyDecision` already has judge fields |

---

## 11. Out of Scope (v1)

- **Embeddings** — No embedding-based semantic provider matching. Pure text-prompt classification.
- **Decision persistence** — In-memory cache only. Decisions do not survive restarts. Can add SQLite persistence later if needed.
- **Judge prompt tuning / fine-tuning** — PRD §15 defers this. Audit logs are structured to enable it later.
- **Judge for proxy selection** — Judge picks provider + fallback only, not proxy. Proxy routing stays in Tier 1.
- **Judge for DLP policy** — Judge does not influence DLP outbound/inbound rules.
- **Quota-aware judge decisions** — PRD §18.6 notes "quota-aware re-ordering does not override an explicit LLM judge decision above the confidence threshold." The judge does not consult quota state; quota filtering happens downstream in `_execute_with_fallback()` and can still remove a judge-suggested provider if it's circuit-broken or quota-exhausted.
