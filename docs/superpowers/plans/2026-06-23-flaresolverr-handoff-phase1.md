# FlareSolverr Session Handoff — Phase 1

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development

**Goal:** When CDP Chrome hits a bot block, automatically solve via FlareSolverr, extract cookies, store them in the session manager, and retry CDP Chrome with the cookies for a full render.

**Architecture:** FlareSolverr solves the challenge → `solution.cookies` extracted → stored in session manager under `flaresolverr/<host>` → CDP Chrome retries with injected cookies → full page content returned. Cached sessions skip the FlareSolverr phase on subsequent requests.

**Tech Stack:** Existing FlareSolverr adapter, CDP Chrome adapter, SessionManager, `ExtractOptions.session_cookies`, `_execute_with_fallback`

**Design doc:** `docs/superpowers/specs/2026-06-23-flaresolverr-handoff-design.md`

---

### Task 1: Add `cookies` field to ExtractResult

**Files:**
- Modify: `src/webgateway/providers/base.py` — add `cookies` field to `ExtractResult`

Add after `status_code`:
```python
    cookies: list[dict] | None = None
```

**Commit:**
```
feat(providers): add cookies field to ExtractResult
```

---

### Task 2: Extract cookies from FlareSolverr response

**Files:**
- Modify: `src/webgateway/providers/flaresolverr.py` — extract `solution.cookies`

In the `extract()` method, after extracting `html` and `final_url`:

```python
solution = data.get("solution") or {}
html = str(solution.get("response", ""))
final_url = str(solution.get("url", url))
cookies = solution.get("cookies")  # ← NEW
```

Add to the return:
```python
return ExtractResult(
    content=html,
    format="html",
    url=final_url,
    cookies=cookies,
)
```

**Unit tests:** Add `test_extract_returns_cookies` — mock a FlareSolverr response with `solution.cookies`, verify they appear on `ExtractResult.cookies`.

**Commit:**
```
feat(providers): extract cookies from FlareSolverr solution
```

---

### Task 3: Integrate session handoff into fallback loop

**Files:**
- Modify: `src/webgateway/service.py` — orchestrate the two-phase flow

This is the core change. In `_execute_with_fallback`, when a bot block is detected:

1. Generate a profile name from the URL host
2. Check if a cached session exists in the session manager
3. If cached, inject cookies and retry via CDP Chrome
4. If not cached, try FlareSolverr to solve the challenge
5. Extract cookies from FlareSolverr's result
6. Store cookies in the session manager
7. Retry CDP Chrome with injected cookies
8. On success, return result

The `_execute_with_fallback` method needs access to the `SessionManager`. It already has `self._session_manager`. Check if it's None-safe.

**Key code path in `_execute_with_fallback`:**

```python
if _is_bot_block(content):
    host = urlparse(url).hostname
    profile = f"flaresolverr/{host}"
    
    # Check for cached session
    cached_session = await self._session_manager.load_session(profile) if self._session_manager else None
    if cached_session and cached_session.cookies:
        # Inject cookies and retry CDP Chrome
        candidates.insert(0, "cdp_chrome")
        # Signal to caller to use these cookies
        continue
    
    # No cached session — solve via FlareSolverr
    if "flaresolverr" not in candidates and self._provider_registry.has("flaresolverr"):
        candidates.append("flaresolverr")
        continue
```

This is a simplification — the actual implementation needs to:
- Pass cookies from session manager into the provider adapter's `extract()` call
- Get cookies back from FlareSolverr's result
- Store them in session manager after a successful solve

The `operation` lambda that calls `provider.extract()` doesn't currently accept cookies. The flow needs restructuring so cookies can be injected.

A cleaner approach: modify the `operation` to accept `ExtractOptions` with cookies. The caller already builds `ExtractOptions` before passing to `_execute_with_fallback` — inject cookies there.

**Commit:**
```
feat(core): two-phase bot bypass — flaresolverr solve → cdp chrome render
```

---

### Task 4: Unit tests

**Files:**
- Modify: `tests/unit/test_flaresolverr.py` — add cookie extraction test
- Create: `tests/unit/test_bot_detection.py` — test `_is_bot_block` patterns, test fallback injection logic

**Commit:**
```
test: flaresolverr cookies and bot detection fallback
```

---

### Task 5: Integration smoke test

**Files:** None

- Rebuild stack
- Test with a site that previously returned thin shell (etsy, bestbuy)
- Verify CDP Chrome retry with cookies returns full content
- Verify repeated calls cache and skip FlareSolverr phase

---

## Execution order

```
Task 1: ExtractResult.cookies  (base.py)
  ↓
Task 2: FlareSolverr cookie extraction  (flaresolverr.py)
  ↓
Task 3: Fallback orchestration  (service.py)
  ↓
Task 4: Tests
  ↓
Task 5: Integration smoke test
```
