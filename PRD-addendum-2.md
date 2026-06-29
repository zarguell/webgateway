# serpLLM PRD Addendum v0.4 (final)

**Date:** 2026-06-17
**Supplements:** PRD v0.1 + Addendum v0.3
**Supersedes:** All prior v0.4 drafts
**Status:** Pre-development

***

## Section 20 — Stealth Browser Services

### 20.1 Purpose

Standard headless browsers are detectable through TLS fingerprinting, canvas/WebGL fingerprinting, behavioral biometrics, and navigator property leakage. Both stealth options below patch Firefox at the **C++ source level** rather than using JavaScript shims — spoofed values emerge through normal Gecko browser paths, making them invisible to `.toString()` checks and getter hijacking that defeat JS-shim approaches.

### 20.2 Stealth Browser Comparison

Both tools are **Firefox-based and Python-only**. The critical distinction is maintenance currency:

| Metric | invisible_playwright | Camoufox |
|---|---|---|
| Firefox version | 150 (weekly releases) | Stale (~1 year gap) |
| Patching approach | C++ source level | C++ source level |
| reCAPTCHA v3 score | 0.90 | ~0.3–0.5 |
| FingerprintJS Pro | ✅ Not detected | ⚠️ Sometimes detected |
| CreepJS "lies" | ✅ 0 | ⚠️ Increasing drift |
| Canvas/WebGL/Audio | ✅ Current Firefox | ⚠️ Drift vs Firefox 150 |
| SOCKS5 auth | ✅ Native patch | ❌ Not supported |
| Request interception | ✅ Clean | ⚠️ ~90% decode errors |
| Playwright agent sandboxing | ✅ Inherited | ✅ Original feature |
| BrowserForge fingerprints | ✅ Inherited | ✅ Original feature |
| Human mouse movement | ✅ Inherited | ✅ Original feature |
| License | MIT | MPL |

The reCAPTCHA gap (0.90 vs 0.3–0.5) is the decisive metric for production use. Camoufox's stale Firefox base means its canvas, WebGL, and audio fingerprints no longer match what real-world Firefox 150 produces — CreepJS detects this drift with increasing frequency. The ~90% response body decode error bug makes Camoufox unreliable for any scraping requiring response interception.

### 20.3 Recommendation

**invisible_playwright is the primary stealth browser.** It applies the same C++ patching philosophy that made Camoufox well-regarded, on a current weekly-updated Firefox build, with dramatically better detection scores across all major WAFs.

**Camoufox is supported as an optional fallback only** — for operators who already have it integrated and are not targeting Cloudflare or DataDome. For new deployments there is no reason to prefer Camoufox over invisible_playwright.

Notable features invisible_playwright inherits from Camoufox's design:
- Playwright agent JS sandboxing — prevents `window.__playwright__binding__` detection
- BrowserForge statistically realistic fingerprint distributions (e.g. Linux users 5% of pool, matching real-world traffic ratios)
- Internally consistent fingerprints — no Windows UA paired with Apple M1 GPU
- Human-like mouse movement algorithm

### 20.4 Container Design

Sidecar containers, never embedded in the gateway image. invisible_playwright is under the `stealth` profile. Camoufox is under a separate `stealth-fallback` profile — it does not start when running `--profile stealth`, only when explicitly opted in:

```yaml
services:
  invisible-playwright:
    image: serpllm/invisible-playwright:latest
    profiles: ["stealth", "browsers"]
    ports: ["3001:3001"]
    environment:
      - MAX_CONCURRENT_SESSIONS=3
      - SESSION_TIMEOUT=300
      - FINGERPRINT_ROTATE=true
    volumes:
      - ./sessions/invisible-playwright:/app/sessions
    deploy:
      resources:
        limits:
          memory: 2g
        reservations:
          memory: 512m
    restart: unless-stopped

  camoufox:
    image: serpllm/camoufox:latest
    profiles: ["stealth-fallback"]
    ports: ["3002:3002"]
    environment:
      - MAX_CONCURRENT_SESSIONS=2
      - FINGERPRINT_POOL_SIZE=10
      - FINGERPRINT_ROTATE=true
    volumes:
      - ./sessions/camoufox:/app/sessions
    deploy:
      resources:
        limits:
          memory: 2g
        reservations:
          memory: 512m
    restart: unless-stopped
```

Gateway environment references:

```yaml
# docker-compose.yml gateway service environment
STEALTH_PLAYWRIGHT_URL: http://invisible-playwright:3001
STEALTH_CAMOUFOX_URL: http://camoufox:3002
```

### 20.5 Provider Adapter Registration

```yaml
providers:
  invisible_playwright:
    type: scrape
    base_url: http://invisible-playwright:3001
    stealth: true
    engine: firefox
    cost_units_per_call: 0.8
    specialization: stealth_primary

  camoufox:
    type: scrape
    base_url: http://camoufox:3002
    stealth: true
    engine: firefox
    cost_units_per_call: 0.8
    specialization: stealth_fallback
    warnings:
      - "Stale Firefox base — not recommended for Cloudflare or DataDome targets"
      - "Response interception unreliable (~90% decode errors)"
```

`warnings` surfaces in `GET /providers` so operators are informed at runtime, not just at config time.

### 20.6 Policy Routing

```yaml
policies:
  - name: stealth_escalation
    match:
      on_error_class: ["bot_detected", 403]
      prior_providers_tried: [jina, firecrawl]
    scrape_provider: invisible_playwright
    fallback_chain: [invisible_playwright, zyte]
    proxy: residential_us

  - name: advanced_waf
    match:
      domain_glob: ["*.cloudflare-protected.com", "*.datadome-protected.com"]
    scrape_provider: invisible_playwright
    fallback_chain: [invisible_playwright, zyte]
    proxy: residential_us

  - name: onion_routing
    match:
      domain_glob: ["*.onion"]
    scrape_provider: invisible_playwright
    proxy: tor_socks5
    # camoufox excluded — no SOCKS5 auth support
```

LLM judge `reasoning_tag` examples: `"stealth_escalation"`, `"recaptcha_site_detected"`, `"advanced_waf_target"`, `"fingerprint_evasion_required"`.

### 20.7 Fingerprint Management

Each stealth browser service maintains a pool of distinct Firefox fingerprint profiles. The gateway passes a `fingerprint_id` or requests rotation per-request. Rotation policy prevents reuse of the same fingerprint on the same domain within a configurable window:

```yaml
stealth:
  fingerprint_rotation:
    same_domain_window_seconds: 3600
    pool_size: 10
```

Request to browser service:

```json
POST http://invisible-playwright:3001/scrape
{
  "url": "https://...",
  "proxy": "http://residential_us:24000",
  "fingerprint": "rotate",
  "session_id": "wsj_session_abc",
  "wait_for_selector": ".article-body",
  "timeout": 30000
}
```

### 20.8 Memory Considerations

Firefox-based stealth browsers consume significantly more RAM than Chromium-based alternatives. `MAX_CONCURRENT_SESSIONS` should be tuned against available memory — 3 concurrent Firefox sessions is a reasonable default for a 2GB limit. Resource limits in Docker Compose (see 20.4) are required, not optional — an unbounded Firefox pool will OOM the host.

### 20.9 SOCKS5 Proxy Note

invisible_playwright supports SOCKS5 authentication via its C++ patch. Camoufox does not. For any policy routing through a SOCKS5 proxy, only invisible_playwright or non-stealth providers should appear in the fallback chain. Camoufox would silently fail SOCKS5 auth — not return an error, just fail to connect through the proxy.

***

## Section 21 — Cookie Bucket (Session Store)

### 21.1 Purpose

Authenticated scraping of paywalled or login-gated content requires persistent browser sessions — cookies, localStorage tokens, and session state. The Cookie Bucket is a named, encrypted session store that any browser service (stealth or standard) can attach to a request. Sessions are created once and reused indefinitely until expiry or invalidation.

This is the mechanism that makes "I have a WSJ subscription" work in the gateway — one authenticated Firefox session shared across all agent scrape requests for that domain, without the agent knowing or caring about session management.

### 21.2 Session Store Design

Sessions stored as encrypted files on a Docker volume, keyed by `session_id`. Encryption via Fernet (Python-native, zero additional dependencies):

```
/sessions/
  invisible-playwright/
    wsj_session_abc.enc
    nytimes_session_xyz.enc
    ft_session_def.enc
  camoufox/
    legacy_session_ghi.enc
```

Each session file contains:
- Cookies (name, value, domain, path, expiry, secure, httpOnly)
- localStorage entries (optional)
- Firefox user-agent string used during login — must match on reuse
- Fingerprint profile ID used during login — must match on reuse
- Browser service binding (`invisible_playwright` | `camoufox`)
- Creation timestamp
- Last-used timestamp
- Expiry timestamp (null = no expiry)
- Domain binding — session only usable for matching domain
- Proxy binding — optional, session always routed through named proxy
- `strict_proxy` flag

### 21.3 Session Lifecycle

**Creation — manual (v1):**

```
POST /admin/sessions/create
  Body: {
    "session_id": "wsj_session_abc",
    "browser": "invisible_playwright",
    "domain": "wsj.com",
    "cookies": [...],
    "user_agent": "Mozilla/5.0 (X11; Linux x86_64; rv:150.0) Gecko/20100101 Firefox/150.0",
    "fingerprint_id": "fp_03",
    "expiry": "2026-09-01T00:00:00Z",
    "proxy_binding": "residential_us",
    "strict_proxy": true
  }
```

User-agent strings must be Firefox strings — these sessions are created and replayed in Firefox-based browsers. A Chrome user-agent in a stealth Firefox session creates a detectable mismatch. Firefox 150 user-agent strings should be used for invisible_playwright sessions.

**Creation — automated login flow (post-v1):**
Gateway triggers invisible_playwright (preferred for form interaction complexity) to navigate to a login URL, fill credentials from a secrets store, and capture the resulting session state. Deferred due to credential management complexity.

**Reuse:**
Any scrape request with `session_profile: "wsj_session_abc"` causes the gateway to:
1. Load and decrypt the session file
2. Validate domain binding, expiry, and browser service match
3. Pass cookies + Firefox user-agent + fingerprint_id to the correct browser service
4. Browser service restores session state before navigating to target URL

**Browser service matching enforcement:**
A session created in invisible_playwright cannot be used by Camoufox without re-authentication — Firefox profile state is not portable between the two:

```
session.browser = "invisible_playwright"
request.scrape_provider = "camoufox"
→ Error: session_browser_mismatch
  Use invisible_playwright for this request, or create a new session in camoufox
```

**Invalidation — manual:**

```
POST /admin/sessions/invalidate
  Body: { "session_id": "wsj_session_abc" }
        { "domain": "wsj.com" }           // all sessions for domain
        { "browser": "camoufox" }         // all sessions for a browser service
```

**Invalidation — automatic triggers:**
- Login wall detected by content quality validator
- HTTP 401 from target
- Session expiry timestamp reached
- Fingerprint profile ID no longer exists in browser service pool (e.g. after container rebuild)

### 21.4 Session-Proxy Binding

Sessions optionally bound to a specific named proxy — critical for sites that tie session validity to IP geolocation. If `strict_proxy: true` and the bound proxy is unavailable, the request fails with a clear error rather than routing through a different proxy (IP mismatch would invalidate the session server-side or trigger re-authentication):

```yaml
# In session metadata
proxy_binding: residential_us
strict_proxy: true
```

### 21.5 Session-Authenticated Cache Behavior

Session-authenticated responses are **never cached**. `cache.write: false` and `cache.read: false` are automatically enforced when `session_profile` is present in the request — prevents one agent's authenticated response from being served to a different request context or agent:

```
request.session_profile present
→ cache.write = false (enforced, not overridable)
→ cache.read = false (session state may have changed since last fetch)
```

### 21.6 Admin Session Endpoints

```
POST /admin/sessions/create
GET  /admin/sessions
  Returns: [{ session_id, domain, browser, engine, created_ts,
              last_used_ts, expiry, proxy_binding, strict_proxy,
              cookie_count, use_count }]
           // cookie values never returned

GET  /admin/sessions/{session_id}/status
  Returns: { valid, expired, domain_bound, browser, fingerprint_id,
             last_used_ts, use_count, proxy_binding }

POST /admin/sessions/invalidate
  Body: { "session_id"? | "domain"? | "browser"? }

POST /admin/sessions/{session_id}/refresh
  Body: { "cookies": [...] }     // re-import updated cookies from Firefox devtools
                                 // preserves all other session metadata
```

Cookie values are write-only after creation — never returned by any API endpoint.

### 21.7 Content Quality Validator Additions (supplements Section 17.6)

Additional detection patterns for session expiry — added to `invalidation_triggers`:

```yaml
- condition: content_contains: [
    "Sign in", "Log in to continue", "Subscribe to read",
    "Create an account", "Your session has expired",
    "Please log in", "Access restricted"
  ]
  action: invalidate_session_and_fail
```

On login wall detection:
1. Auto-invalidate the session
2. Write `session_expired: true` to audit log
3. Return structured error to agent — do not silently retry with a different provider, which would return paywalled/gated content and mislead the agent

### 21.8 Audit Log Fields (additions to PRD Section 4.7)

```json
{
  "session_profile": "wsj_session_abc",
  "session_valid": true,
  "session_expired": false,
  "fingerprint_id": "fp_03",
  "browser_service": "invisible_playwright",
  "browser_engine": "firefox",
  "firefox_version": "150"
}
```

***

## Full Interaction Flow

Authenticated paywalled scrape, end-to-end:

```
Agent → POST /scrape {
  url: "https://wsj.com/article/...",
  format: "markdown",
  session_profile: "wsj_session_abc"
}
  ↓
Auth middleware → validate Bearer token → resolve api_key_id
  ↓
Policy engine
  Tier 1 → matches "paywalled_news" rule → provider: invisible_playwright
  Cache  → read skipped (session_profile present)
  ↓
Session store
  → decrypt wsj_session_abc
  → validate: domain ✅, expiry ✅, browser match ✅
  → resolve proxy_binding → residential_us
  ↓
Proxy injector → apply http://residential_us:24000
  ↓
invisible_playwright adapter →
  POST http://invisible-playwright:3001/scrape {
    url: "https://wsj.com/article/...",
    cookies: [...],
    user_agent: "Mozilla/5.0 ... Firefox/150.0",
    fingerprint_id: "fp_03",
    proxy: "http://residential_us:24000",
    wait_for_selector: ".article-body"
  }
  ↓
Content quality validator
  → length check ✅
  → JS blob check ✅
  → login wall check ✅
  ↓
Response normalizer → clean markdown output
  ↓
Cache write → skipped (session_profile present, enforced)
  ↓
Audit logger → write structured log line
  ↓
Agent ← { content, format: "markdown", provider_used: "invisible_playwright",
           cached: false, browser_engine: "firefox", session_profile: "wsj_session_abc" }

--- Failure path ---
Login wall detected in quality validator:
  → invalidate wsj_session_abc
  → audit log: session_expired: true
  → return to agent: { error: "session_expired", session_id: "wsj_session_abc",
                        message: "Login wall detected. Session invalidated. Refresh cookies." }
  // does NOT retry with jina/firecrawl — would return paywalled content
```

***

## Config Schema Additions (supplements PRD Section 11 and Addendum v0.3)

```yaml
stealth:
  fingerprint_rotation:
    same_domain_window_seconds: 3600
    pool_size: 10

sessions:
  store_path: /app/sessions
  encryption_key: ${SESSION_ENCRYPTION_KEY}
  auto_invalidate_on_login_wall: true
  strict_proxy_binding: true            # global default, overridable per session

providers:
  invisible_playwright:
    type: scrape
    base_url: http://invisible-playwright:3001
    stealth: true
    engine: firefox
    firefox_version: "150"
    cost_units_per_call: 0.8
    specialization: stealth_primary

  camoufox:
    type: scrape
    base_url: http://camoufox:3002
    stealth: true
    engine: firefox
    cost_units_per_call: 0.8
    specialization: stealth_fallback
    warnings:
      - "Stale Firefox base — not recommended for Cloudflare or DataDome"
      - "Response interception unreliable (~90% decode errors)"
      - "No SOCKS5 auth support"
```

***

## Updated Build Order Additions (inserts after step 10 of Addendum v0.3)

- **10a.** invisible_playwright REST wrapper image + scrape adapter
- **10b.** Camoufox REST wrapper image + scrape adapter (fallback-only profile)
- **10c.** Cookie Bucket — Fernet-encrypted session store, domain binding, browser binding, CRUD
- **10d.** Session-proxy binding enforcement + strict proxy mode
- **10e.** Firefox user-agent + fingerprint_id passthrough to browser services
- **10f.** Fingerprint rotation policy — per-domain window tracking
- **10g.** Login wall detection patterns in content quality validator
- **10h.** Auto-invalidation on login wall + structured error response
- **10i.** Session cache bypass enforcement (`write/read: false` when session_profile present)
- **10j.** Provider `warnings` field in `GET /providers` response
- **10k.** Admin session endpoints — create, list, status, invalidate, refresh
- **10l.** Docker Compose stealth profiles — `stealth` (invisible_playwright) and `stealth-fallback` (camoufox), both with memory limits
