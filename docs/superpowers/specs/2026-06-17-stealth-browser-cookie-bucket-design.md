# Stealth Browser Services + Cookie Bucket Design

**Date:** 2026-06-17
**Status:** Draft
**Source:** PRD Addendum v0.4 (§20–§21) + PRD Addendum v0.3 (§17)

---

## 1. Scope

Implement Sections 20 (Stealth Browser Services) and 21 (Cookie Bucket / Session Store) from PRD Addendum v0.4.

**In scope:**
- `invisible_playwright` provider adapter — REST wrapper for the stealth browser sidecar
- Provider config schema extensions (`stealth`, `engine`, `firefox_version`, `specialization`, `warnings`, `cost_units_per_call`)
- Cookie Bucket — Fernet-encrypted session store with domain/proxy/browser binding
- Session admin CRUD endpoints (create, list, status, invalidate, refresh)
- Session resolution in GatewayService — validation, cache bypass, proxy binding enforcement
- Login wall detection in content quality validator + auto-invalidation
- Provider `warnings` field in `GET /providers`
- Audit log fields for session/browser metadata
- Docker Compose `stealth` profile for invisible-playwright sidecar

**Out of scope:**
- Camoufox provider adapter, config, or Docker profile
- Automated login flow (credential management deferred to post-v1)

**Camoufox co-existence:** All camoufox-specialized code paths are gated behind `name == "camoufox"` or `specialization == "stealth_fallback"`. Adding it later requires: one adapter file, one registry entry, one `stealth-fallback` Docker Compose profile. No structural changes.

---

## 2. Provider Config Extensions

Extend `ProviderConfig` with optional fields used by browser-based providers:

```python
class ProviderConfig(BaseModel):
    # ... existing fields ...
    stealth: bool = False
    engine: Literal["firefox", "chromium", None] = None
    firefox_version: str | None = None
    specialization: str | None = None       # "stealth_primary", "stealth_fallback"
    warnings: list[str] = Field(default_factory=list)
    cost_units_per_call: float = 1.0
```

All fields are optional with safe defaults — existing providers are unaffected. `engine`, `firefox_version`, `specialization`, `warnings`, `cost_units_per_call` are metadata fields surfaced via `GET /providers` but not used in routing logic.

New config section in `GatewayConfig`:

```python
class StealthConfig(BaseModel):
    fingerprint_rotation:
        same_domain_window_seconds: int = 3600
        pool_size: int = 10
```

New fields in `SessionsConfig`:

```python
class SessionsConfig(BaseModel):
    store_path: str = "/app/sessions"
    encryption_key: str | None = None      # required at runtime for Fernet
    auto_invalidate_on_login_wall: bool = True
    strict_proxy_binding: bool = True
```

---

## 3. Provider Metadata Extensions

Add `warnings` field to `ProviderMetadata`:

```python
@dataclass
class ProviderMetadata:
    # ... existing fields ...
    warnings: list[str] = field(default_factory=list)
```

Plus new metadata fields: `stealth`, `engine`, `firefox_version`, `specialization`, `cost_units_per_call`. Exposed via `GET /providers` alongside existing metadata.

---

## 4. ExtractOptions Extensions

```python
@dataclass
class ExtractOptions:
    format: str = "markdown"
    proxy_url: str | None = None
    wait_for_selector: str | None = None
    session_cookies: dict[str, str] | None = None
    session_id: str | None = None           # NEW — pass session_id to adapter
    fingerprint_id: str | None = None       # NEW — fingerprint profile ID
    user_agent: str | None = None           # NEW — Firefox UA override
    timeout: int = 15
```

---

## 5. Invisible Playwright Provider Adapter

**File:** `src/webgateway/providers/invisible_playwright.py`

Adapter for the invisible_playwright REST sidecar at `http://invisible-playwright:3001`.

- Implements `ProviderAdapter` protocol
- `search()` — raises `ProviderError` with `error_class="not_supported"` (stealth browsers are extract-only)
- `extract()` — calls `POST /scrape` on the sidecar:

```json
POST /scrape
{
  "url": "...",
  "proxy": "http://...",
  "fingerprint": "rotate",        // or a specific fingerprint_id
  "session_id": "wsj_session_abc", // optional
  "cookies": [...],                // from session store, if applicable
  "user_agent": "Mozilla/5.0 ...",
  "wait_for_selector": ".article-body",
  "timeout": 30000
}
```

- Response: `{ "content": "...", "format": "markdown", "url": "...", "title": "..." }`
- `health_check()` — hits `GET /health` on the sidecar
- Hostname extraction for content title uses `urllib.parse`

**Configuration in registry:** Added to `_create_adapter()`:

```python
if name == "invisible_playwright":
    return InvisiblePlaywrightAdapter(
        base_url=cfg.base_url or "http://invisible-playwright:3001",
        timeout=cfg.timeout or 15,
    )
```

---

## 6. Session Store (Cookie Bucket)

**Module:** `src/webgateway/sessions/`

### 6.1 Session Data Model (`sessions/models.py`)

```python
@dataclass
class CookieEntry:
    name: str
    value: str
    domain: str
    path: str = "/"
    expiry: float | None = None
    secure: bool = True
    http_only: bool = True

@dataclass
class SessionData:
    """Full session state — serialised to encrypted JSON on disk."""
    session_id: str
    browser_service: str          # "invisible_playwright" | "camoufox"
    domain: str                    # bound domain (e.g. "wsj.com")
    cookies: list[CookieEntry]
    user_agent: str
    fingerprint_id: str
    created_ts: float
    last_used_ts: float
    expiry_ts: float | None = None
    proxy_binding: str | None = None
    strict_proxy: bool = False
    use_count: int = 0
    local_storage: dict[str, str] | None = None

@dataclass
class SessionInfo:
    """Public metadata returned by admin API — no cookie values."""
    session_id: str
    domain: str
    browser: str
    engine: str                    # "firefox"
    created_ts: float
    last_used_ts: float
    expiry: float | None
    proxy_binding: str | None
    strict_proxy: bool
    cookie_count: int
    use_count: int
```

### 6.2 Session Store (`sessions/store.py`)

```python
class SessionStore:
    """Fernet-encrypted session file store. One file per session."""

    def __init__(self, store_path: str, encryption_key: str):
        # Initialise Fernet cipher from key (32-byte base64-encoded)
        # Ensure store_path exists

    def save(self, session: SessionData) -> None:
        """Encrypt and write session file. Path: {store_path}/{session_id}.enc"""

    def load(self, session_id: str) -> SessionData:
        """Read and decrypt session file. Raises SessionNotFound."""

    def delete(self, session_id: str) -> None:
        """Remove session file. No-op if missing."""

    def list_sessions(self) -> list[SessionInfo]:
        """Iterate all .enc files, decrypt metadata only (partial load)."""

    def exists(self, session_id: str) -> bool: ...
```

Serialisation: `orjson.dumps(session_data)` → encrypt → write. Fernet handles integrity (HMAC).

### 6.3 Session Manager (`sessions/manager.py`)

```python
class SessionError(Exception):
    def __init__(self, error_class: str, message: str):
        self.error_class = error_class  # "session_expired" | "session_browser_mismatch" | ...

class SessionManager:
    """Session lifecycle management — wraps store with validation."""

    def __init__(self, store: SessionStore, config: SessionsConfig): ...

    async def resolve(
        self,
        session_id: str,
        *,
        provider_name: str,
        domain: str,
        proxy_name: str | None,
    ) -> SessionData:
        """Load session, validate all bindings, update last_used_ts.
        Raises SessionError on any validation failure."""

    async def invalidate(self, *, session_id: str | None = None,
                          domain: str | None = None,
                          browser: str | None = None) -> int:
        """Invalidate matching sessions. Returns count."""

    async def touch(self, session_id: str) -> None:
        """Update last_used_ts and increment use_count."""
```

**Validation checks in `resolve()`:**
1. Session file exists and decrypts successfully
2. `expiry_ts` not reached
3. `browser_service` matches `provider_name`
4. `domain` matches request URL's hostname (exact or subdomain match)
5. If `proxy_binding` is set and `strict_proxy` is true, `proxy_name` must match

---

## 7. GatewayService Integration

### 7.1 Extract Flow (modified)

In `GatewayService.extract()`, between policy evaluation and provider dispatch:

```
1. Policy engine → RoutingDecision
2. IF request.session_profile is set:
   a. Extract domain from request.url
   b. session_manager.resolve(session_id=request.session_profile,
        provider_name=decision.provider,
        domain=extracted_domain,
        proxy_name=decision.proxy)
   c. Force cache_read=False, cache_write=False (overrides any request.cache)
   d. Set session cookies + fingerprint_id + user_agent on ExtractOptions
3. ELSE: normal flow
4. Dispatch to provider
5. On response:
   a. session_manager.touch(session_id)
   b. If login wall detected (quality validator):
      - session_manager.invalidate(session_id=...)
      - Return structured error: { error: "session_expired", session_id: "..." }
```

### 7.2 Cache Bypass Enforcement

```python
if request.session_profile is not None:
    cache_read = False
    cache_write = False
    # User-provided cache overrides are ignored when session_profile is set
```

Enforced *after* reading `request.cache` so user intent is visible in logs but overridden.

### 7.3 Config + Wiring in main.py

```python
session_store = SessionStore(
    store_path=config_manager.config.sessions.store_path,
    encryption_key=config_manager.config.sessions.encryption_key,
)
app.state.session_store = session_store

session_manager = SessionManager(session_store, config_manager.config.sessions)
app.state.session_manager = session_manager

gateway_service = GatewayService(
    # ... existing args ...
    session_manager=session_manager,
)
```

`GatewayService` constructor gains optional `session_manager: SessionManager | None = None`.

---

## 8. Admin Session Endpoints

**File:** `src/webgateway/routes/sessions_admin.py`

All endpoints require `verify_admin`.

| Method | Path | Body / Params | Description |
|--------|------|---------------|-------------|
| POST | `/admin/sessions/create` | `SessionCreateRequest` | Create encrypted session file |
| GET | `/admin/sessions` | — | List all sessions (metadata only) |
| GET | `/admin/sessions/{session_id}/status` | — | Session validity + metadata |
| POST | `/admin/sessions/invalidate` | `session_id?`, `domain?`, `browser?` | Invalidate sessions |
| POST | `/admin/sessions/{session_id}/refresh` | `cookies: [...]` | Replace cookies, keep metadata |

**SessionCreateRequest schema:**
```python
class SessionCreateRequest(BaseModel):
    session_id: str
    browser: str                        # "invisible_playwright"
    domain: str
    cookies: list[CookieEntrySchema]    # name, value, domain, path, expiry, secure, httpOnly
    user_agent: str
    fingerprint_id: str
    expiry: datetime | None = None
    proxy_binding: str | None = None
    strict_proxy: bool = False
```

Cookie values are **write-only** — never returned by any GET endpoint. The `GET /admin/sessions` response includes `SessionInfo` only (no cookie values, no `local_storage`).

---

## 9. Content Quality — Login Wall Detection

Login wall detection is a **separate concern** from cache invalidation triggers. Session invalidation is not a cache operation, and the action "invalidate_session_and_fail" doesn't fit the cache trigger model.

### 9.1 Configuration

```yaml
sessions:
  login_wall_patterns:
    - "Sign in"
    - "Log in to continue"
    - "Subscribe to read"
    - "Create an account"
    - "Your session has expired"
    - "Please log in"
    - "Access restricted"
  auto_invalidate_on_login_wall: true
```

### 9.2 Implementation

A new method `_check_login_wall(content: str) -> bool` on `GatewayService` checks the response content against configured patterns.

In `GatewayService.extract()`, after `_execute_with_fallback` returns:
1. If `request.session_profile` is set AND `auto_invalidate_on_login_wall` is true:
   a. Check response content against login wall patterns
   b. If matched:
      - Call `session_manager.invalidate(session_id=request.session_profile)`
      - Write audit entry with `session_expired: true`
      - Raise `SessionError("session_expired")` → HTTP 419
      - **Do NOT retry with fallback providers** — would return paywalled content

This happens **outside** the `_execute_with_fallback` loop, so login wall detection never triggers a fallback provider retry. Non-session requests skip this check entirely.

Non-session requests are unaffected — login wall patterns only fire when `session_profile` is active.

---

## 10. Audit Log Fields

Extend `AuditEntry` with optional session/browser fields:

```python
@dataclass
class AuditEntry:
    # ... existing fields ...
    session_profile: str | None = None
    session_valid: bool | None = None
    session_expired: bool | None = None
    fingerprint_id: str | None = None
    browser_service: str | None = None
    browser_engine: str | None = None
    firefox_version: str | None = None
```

These are populated only when a session is used.

---

## 11. Provider `warnings` in GET /providers

Add `warnings: list[str]` to `ProviderMetadataInfo` schema:

```python
class ProviderMetadataInfo(BaseModel):
    # ... existing fields ...
    warnings: list[str] = Field(default_factory=list)
    stealth: bool = False
    engine: str | None = None
    firefox_version: str | None = None
    specialization: str | None = None
    cost_units_per_call: float = 1.0
```

Populated from `ProviderConfig.warnings` by the registry during adapter construction.

Each adapter exposes `warnings` via `ProviderMetadata`. For invisible_playwright:

```python
@property
def metadata(self) -> ProviderMetadata:
    return ProviderMetadata(
        name="invisible_playwright",
        self_hosted=True,
        data_retention_days=0,
        trains_on_queries=False,
        gdpr_compliant=True,
        data_residency=["local"],
        capabilities=["extract"],
        warnings=self._warnings,
        stealth=True,
        engine="firefox",
        firefox_version=self._firefox_version,
        specialization="stealth_primary",
        cost_units_per_call=self._cost_units_per_call,
    )
```

---

## 12. Docker Compose

Add to `docker-compose.yml` under `services`:

```yaml
invisible-playwright:
  image: webgateway/invisible-playwright:latest
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
```

Gateway service environment references:

```yaml
services:
  gateway:
    environment:
      STEALTH_PLAYWRIGHT_URL: http://invisible-playwright:3001
```

The `invisible-playwright` service starts only when `--profile stealth` or `--profile browsers` is passed to `docker compose up`.

---

## 13. Config.yaml Additions

```yaml
stealth:
  fingerprint_rotation:
    same_domain_window_seconds: 3600
    pool_size: 10

providers:
  invisible_playwright:
    base_url: http://invisible-playwright:3001
    stealth: true
    engine: firefox
    firefox_version: "150"
    cost_units_per_call: 0.8
    specialization: stealth_primary

sessions:
  store_path: sessions
  encryption_key: ${SESSION_ENCRYPTION_KEY}
  auto_invalidate_on_login_wall: true
  strict_proxy_binding: true
```

---

## 14. Files Changed / Created

| File | Action | Purpose |
|------|--------|---------|
| `src/webgateway/providers/invisible_playwright.py` | CREATE | Provider adapter |
| `src/webgateway/sessions/__init__.py` | CREATE | Package init |
| `src/webgateway/sessions/models.py` | CREATE | Session data models |
| `src/webgateway/sessions/store.py` | CREATE | Fernet-encrypted store |
| `src/webgateway/sessions/manager.py` | CREATE | Session lifecycle |
| `src/webgateway/routes/sessions_admin.py` | CREATE | Admin endpoints |
| `src/webgateway/config.py` | MODIFY | ProviderConfig, StealthConfig, SessionsConfig |
| `src/webgateway/providers/base.py` | MODIFY | ExtractOptions, ProviderMetadata |
| `src/webgateway/providers/registry.py` | MODIFY | Register adapter |
| `src/webgateway/service.py` | MODIFY | Session resolution, cache bypass |
| `src/webgateway/schemas.py` | MODIFY | Session schemas, warnings field |
| `src/webgateway/audit.py` | MODIFY | Session audit fields |
| `src/webgateway/main.py` | MODIFY | Wire session manager |
| `src/webgateway/cache/quality.py` | MODIFY | Login wall patterns |
| `tests/unit/` | MODIFY | Tests for new modules |
| `config.yaml` | MODIFY | Stealth config, session config |
| `docker-compose.yml` | MODIFY | Stealth profile service |

---

## 15. Error Handling

| Error Class | HTTP Code | Trigger | Recovery |
|-------------|-----------|---------|----------|
| `session_expired` | 419 | Login wall detected or expiry reached | Refresh cookies via admin endpoint |
| `session_browser_mismatch` | 400 | Session bound to different browser | Use correct provider for session |
| `session_domain_mismatch` | 400 | Session domain ≠ request domain | Create session for correct domain |
| `session_proxy_mismatch` | 400 | Strict proxy binding violated | Route through bound proxy |
| `session_not_found` | 404 | Session file missing | Create session first |
| `session_encryption_error` | 500 | Key mismatch or corrupted file | Re-create session |

All session errors return a structured JSON body with `error` object.

---

## 16. Testing Strategy

- **Unit tests for SessionStore:** encrypt → save → load → verify decrypted data matches original; load nonexistent; corrupt file raises error
- **Unit tests for SessionManager:** domain match/mismatch, expiry, browser mismatch, proxy binding
- **Unit tests for InvisiblePlaywrightAdapter:** mock httpx responses, verify correct request body construction
- **Unit tests for GatewayService session integration:** mock session manager, verify cache bypass, verify audit fields
- **Unit tests for admin endpoints:** CRUD via TestClient, verify cookie values never returned
- **Unit test for quality validator:** login wall patterns match expected strings
