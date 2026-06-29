# FlareSolverr → CDP Chrome Session Handoff

## Problem

FlareSolverr solves Cloudflare/DataDome challenges but returns thin JS-rendered shells. CDP Chrome renders pages fully but can't bypass bot detection. Currently both fail independently — solving the challenge and throwing away the session cookies wastes the solve.

## Solution

Orchestrate a two-phase extraction: FlareSolverr solves the challenge → cookies are extracted → CDP Chrome replays the same URL with the cookies → full page render.

Once solved, the session is cached so repeat visits skip the challenge phase entirely.

## Architecture

```
Agent calls web_extract(url)

Phase 1 — Cookie acquisition (only on first visit or expired session)
  CDP Chrome → bot block detected
    ↓ (auto-detect triggers fallback)
  FlareSolverr extracts url with session_id
    ↓
  FlareSolverr adapter returns solved cookies + final URL
    ↓
  Cookies stored in session manager under profile name "flaresolverr/<host>"
    ↓
  Session ID cached in memory (ttl matched to cookie expiry)

Phase 2 — Full extraction (always)
  CDP Chrome retries url with session_cookies from session manager
    ↓
  Full page rendered, JSON-LD extracted, content trafilatura'd
    ↓
  Returns content + structured_data to agent
```

## Data flow

### FlareSolverr adapter changes

Currently FlareSolverr sends:
```json
{"cmd": "request.get", "url": "https://...", "maxTimeout": 60000}
```

It needs to also support session creation:
```json
{"cmd": "request.get", "url": "https://...", "session": "fs_host_com", "maxTimeout": 60000}
```

And the response already contains cookies:
```json
{
  "status": "ok",
  "solution": {
    "url": "...",
    "response": "<partial HTML>",
    "cookies": [
      {"name": "cf_clearance", "value": "...", "domain": ".host.com"}
    ]
  }
}
```

The adapter needs to:
1. Accept a `session_id` from `ExtractOptions.session_id`
2. Pass it as FlareSolverr's `session` parameter
3. On success, extract `solution.cookies` from the response
4. Return cookies alongside the content so the caller can store them

### Session manager integration

The existing session manager already stores cookie profiles:

```python
# Store cookies
await self._session_manager.save(
    profile_name="flaresolverr/www.etsy.com",
    cookies=[{"name": "...", "value": "...", "domain": "..."}],
    domain="www.etsy.com",
)
```

The profile name convention: `flaresolverr/<host>` — so the same FlareSolverr session can be reused for multiple pages on the same domain within TTL.

### CDP Chrome adapter changes

The adapter already forwards `ExtractOptions.session_cookies` to the sidecar:

```python
if options.session_cookies:
    payload["cookies"] = [
        {"name": k, "value": v}
        for k, v in options.session_cookies.items()
    ]
```

No changes needed here — just needs to receive cookies from the session manager via the existing `ExtractOptions` path.

## Implementation

### Phase 1: Wire FlareSolverr cookie extraction

**Files:**
- `src/webgateway/providers/flaresolverr.py` — extract cookies from solution response
- `src/webgateway/providers/base.py` — add `cookies` field to `ExtractResult`

**Changes:**

```python
# ExtractResult gets a new field
@dataclass
class ExtractResult:
    content: str = ""
    format: str = "markdown"
    url: str = ""
    title: str | None = None
    status_code: int = 200
    cookies: list[dict] | None = None  # ← NEW
```

FlareSolverr adapter extracts cookies from the response:

```python
solution = data.get("solution") or {}
cookies = solution.get("cookies")
return ExtractResult(
    content=html,
    format="html",
    url=final_url,
    cookies=cookies,
)
```

### Phase 2: Wire fallback orchestration

**File:**
- `src/webgateway/service.py` — bot block handling in `_execute_with_fallback`

**Logic:**

```python
if _is_bot_block(content):
    # Try FlareSolverr to get session cookies
    fs_result = await _solve_with_flaresolverr(url)
    if fs_result and fs_result.cookies:
        # Store cookies in session manager
        host = urlparse(url).hostname
        await session_manager.save(
            profile_name=f"flaresolverr/{host}",
            cookies=fs_result.cookies,
            domain=host,
        )
        # Retry CDP Chrome with cookies
        continue  # back to the fallback loop with cookies injected
```

### Phase 3: Session reuse

**File:**
- `src/webgateway/service.py` — extract phase

Before dispatching to CDP Chrome, inject cached cookies:

```python
host = urlparse(url).hostname
session = await session_manager.load(f"flaresolverr/{host}")
if session:
    cookies = {c.name: c.value for c in session.cookies}
    options = ExtractOptions(session_cookies=cookies)
```

## Testing

1. **Unit**: FlareSolverr adapter returns cookies from mock response
2. **Integration**: E2E with real Etsy page — verify CDP Chrome gets cookies from FlareSolverr solve and returns full content
3. **Session reuse**: Second call to same domain skips FlareSolverr phase

## Non-goals

- No new MCP tools or API endpoints
- No changes to the session manager persistence layer (already works)
- No changes to CDP Chrome sidecar (already supports cookies)
- No automatic session expiry tracking beyond existing cookie TTL
