# Auth Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three auth/security gaps: MCP auth doesn't check SQLite-backed keys, admin UI has no CSRF protection, and responses lack security headers.

**Architecture:** Refactor `McpAuthMiddleware` to use the same multi-source `_find_key()` resolver as the REST auth. Add CSRF token validation to admin UI state-changing endpoints. Add a middleware that sets security headers (HSTS, CSP, X-Frame-Options, X-Content-Type-Options) on all responses.

**Tech Stack:** `secrets.token_hex` for CSRF token generation, Starlette middleware, Jinja2 template updates, `itsdangerous` (already in deps) for token signing.

---

### Task 1: Fix MCP auth to use multi-source key resolution

**Files:**
- Modify: `src/serp_llm/mcp/server.py`

- [ ] **Step 1: Refactor McpAuthMiddleware to use the auth module**

In `src/serp_llm/mcp/server.py`, the `McpAuthMiddleware` class currently calls `self._config_manager.find_auth_key(token)` (line 93), which only checks config-based keys.

Replace the `__init__` and `dispatch` methods to use `_find_key` from `auth.py`:

```python
class McpAuthMiddleware(BaseHTTPMiddleware):
    """Validates Bearer tokens on MCP endpoint requests.

    Uses the same multi-source key resolution as the REST auth:
    1. Config-based keys (legacy ``auth.keys`` in config.yaml)
    2. SQLite-backed keys (KeyStore)
    3. Bootstrap key (env var, only when admin keys table is empty)

    On success, sets the ``mcp_api_key_id`` contextvar so tool functions
    can include the key ID in the audit trail.
    """

    def __init__(
        self,
        app,
        config_manager: ConfigManager,
        key_store: KeyStore | None = None,
    ) -> None:
        super().__init__(app)
        self._config_manager = config_manager
        self._key_store = key_store

    async def dispatch(self, request: Request, call_next):
        header = request.headers.get("Authorization", "")
        parts = header.split(" ", 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return JSONResponse(
                status_code=401,
                content={"error": "Invalid or missing API key"},
            )
        token = parts[1].strip()

        # Use the same multi-source resolver as REST auth
        from serp_llm.auth import _find_key

        # Create a minimal request-like object that _find_key can work with
        # by attaching the components it needs
        key = self._find_key(token)
        if key is None:
            return JSONResponse(
                status_code=401,
                content={"error": "Invalid or missing API key"},
            )
        ctx_token = mcp_api_key_id.set(key.id)
        try:
            return await call_next(request)
        finally:
            mcp_api_key_id.reset(ctx_token)

    def _find_key(self, token: str):
        """Check all auth sources in priority order."""
        # 1. Config-based keys
        key = self._config_manager.find_auth_key(token)
        if key is not None:
            return key

        # 2. SQLite-backed keys
        if self._key_store is not None:
            stored = self._key_store.verify_key(token)
            if stored is not None:
                from serp_llm.config import AuthKey
                return AuthKey(
                    id=stored.id,
                    secret=token,
                    label=stored.label,
                    admin=(stored.role == "admin"),
                )

        # 3. Bootstrap key
        bootstrap = self._check_bootstrap_key(token)
        if bootstrap is not None:
            return bootstrap

        return None

    def _check_bootstrap_key(self, token: str):
        """Bootstrap admin key (valid only when api_keys table is empty)."""
        import os

        bootstrap_secret = os.environ.get("BOOTSTRAP_ADMIN_KEY")
        if not bootstrap_secret:
            return None
        if token != bootstrap_secret:
            return None
        if self._key_store is not None and self._key_store.count_active_admin_keys() > 0:
            return None
        from serp_llm.config import AuthKey
        return AuthKey(
            id="bootstrap",
            secret=bootstrap_secret,
            label="Bootstrap admin key",
            admin=True,
        )
```

Update the import section at the top of the file to include `KeyStore`:

```python
from serp_llm.config import ConfigManager
from serp_llm.dlp import DlpBlockedError
from serp_llm.key_store import KeyStore
from serp_llm.providers.base import ProviderError
```

Update `mount_mcp()` (line 235) to pass the key_store:

```python
def mount_mcp(
    app: FastAPI,
    gateway_service: GatewayService,
    config_manager: ConfigManager,
):
```

And inside the function, update the middleware registration (line 260):

```python
    mcp_app = mcp_server.streamable_http_app()
    key_store: KeyStore | None = getattr(app.state, "key_store", None)
    mcp_app.add_middleware(
        McpAuthMiddleware,
        config_manager=config_manager,
        key_store=key_store,
    )
```

- [ ] **Step 2: Write a unit test for McpAuthMiddleware key resolution**

Create `tests/unit/test_mcp_auth.py`:

```python
"""Tests for MCP auth middleware multi-source key resolution."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from serp_llm.config import AuthKey, ConfigManager
from serp_llm.key_store import KeyStore
from serp_llm.mcp.server import McpAuthMiddleware


def _make_app_with_middleware(
    config_manager: ConfigManager,
    key_store: KeyStore | None = None,
):
    """Build a minimal ASGI app with McpAuthMiddleware for testing."""
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    app = FastAPI()

    @app.get("/mcp/test")
    async def test_endpoint():
        return JSONResponse({"ok": True})

    app.add_middleware(
        McpAuthMiddleware,
        config_manager=config_manager,
        key_store=key_store,
    )
    return app


def test_mcp_auth_accepts_config_key():
    """A valid config-based key should pass MCP auth."""
    cm = MagicMock(spec=ConfigManager)
    cm.find_auth_key.return_value = AuthKey(
        id="key_agent1", secret="valid-token", label="test", admin=False
    )
    app = _make_app_with_middleware(cm)
    client = TestClient(app)
    resp = client.get("/mcp/test", headers={"Authorization": "Bearer valid-token"})
    assert resp.status_code == 200


def test_mcp_auth_rejects_invalid_key():
    """An unknown token should return 401."""
    cm = MagicMock(spec=ConfigManager)
    cm.find_auth_key.return_value = None
    app = _make_app_with_middleware(cm)
    client = TestClient(app)
    resp = client.get("/mcp/test", headers={"Authorization": "Bearer bad-token"})
    assert resp.status_code == 401


def test_mcp_auth_rejects_missing_header():
    """No Authorization header should return 401."""
    cm = MagicMock(spec=ConfigManager)
    app = _make_app_with_middleware(cm)
    client = TestClient(app)
    resp = client.get("/mcp/test")
    assert resp.status_code == 401


def test_mcp_auth_rejects_bad_scheme():
    """Non-Bearer Authorization should return 401."""
    cm = MagicMock(spec=ConfigManager)
    app = _make_app_with_middleware(cm)
    client = TestClient(app)
    resp = client.get("/mcp/test", headers={"Authorization": "Basic dGVzdDp0ZXN0"})
    assert resp.status_code == 401
```

- [ ] **Step 3: Run MCP auth tests**

```bash
source .venv/bin/activate && pytest tests/unit/test_mcp_auth.py -v
```

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/serp_llm/mcp/server.py tests/unit/test_mcp_auth.py
git commit -m "fix(mcp): multi-source auth key resolution for MCP middleware"
```

---

### Task 2: Add CSRF protection to admin UI

**Files:**
- Modify: `src/serp_llm/admin_session.py` (add CSRF token support)
- Modify: `src/serp_llm/routes/admin_ui.py` (add CSRF validation)
- Modify: `src/serp_llm/templates/admin_base.html` (add CSRF token to forms)

- [ ] **Step 1: Add CSRF token generation to AdminSessionManager**

In `src/serp_llm/admin_session.py`, add CSRF token support:

```python
import hashlib
import hmac
import secrets
import time
from datetime import UTC, datetime, timedelta

# Add after the existing imports

_CSRF_TOKEN_TTL_SECONDS = 3600  # 1 hour
```

Add methods to `AdminSessionManager`:

```python
    def generate_csrf_token(self, session_cookie: str) -> str:
        """Generate a signed CSRF token tied to the admin session.

        The token is an HMAC-SHA256 of the session cookie value + timestamp,
        preventing reuse after expiry.
        """
        expires = str(int(time.time()) + _CSRF_TOKEN_TTL_SECONDS)
        msg = f"{session_cookie}:{expires}"
        sig = hmac.new(
            self._signing_key.encode(),
            msg.encode(),
            hashlib.sha256,
        ).hexdigest()
        return f"{expires}.{sig}"

    def verify_csrf_token(self, session_cookie: str, token: str) -> bool:
        """Verify a CSRF token is valid and not expired.

        Returns True if the token matches and is within the TTL window.
        """
        try:
            expires_ts_str, sig = token.split(".", 1)
            expires = int(expires_ts_str)
        except (ValueError, IndexError):
            return False

        # Check expiry
        if time.time() > expires:
            return False

        # Verify signature
        msg = f"{session_cookie}:{expires_ts_str}"
        expected = hmac.new(
            self._signing_key.encode(),
            msg.encode(),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(sig, expected)
```

Add the `_signing_key` property to `AdminSessionManager.__init__`:

It should already exist since `__init__` takes a `secret` parameter. Add the `_csrf_token_ttl`:

```python
    def __init__(self, secret: str | None = None) -> None:
        # ... existing init ...
        self._csrf_token_ttl = _CSRF_TOKEN_TTL_SECONDS
```

- [ ] **Step 2: Add CSRF token to template context and add hidden field**

In `src/serp_llm/routes/admin_ui.py`, modify `_get_common_context` to generate a CSRF token:

```python
def _get_common_context(request: Request) -> dict:
    mgr = _get_session_manager(request)
    session_cookie = request.cookies.get(mgr.cookie_name, "")
    csrf_token = mgr.generate_csrf_token(session_cookie) if session_cookie else ""
    return {
        "request": request,
        "session": getattr(request.state, "admin_session", None),
        "csrf_token": csrf_token,
    }
```

In `src/serp_llm/templates/admin_base.html`, add a hidden CSRF token field inside every `<form>` that performs a POST. Add after the opening `<form>` tag (or at the end, before `</form>`):

```html
<input type="hidden" name="_csrf_token" value="{{ csrf_token }}">
```

- [ ] **Step 3: Validate CSRF token in state-changing POST handlers**

In `src/serp_llm/routes/admin_ui.py`, add a CSRF validation function:

```python
def _verify_csrf(
    mgr: AdminSessionManager, session_cookie: str | None, csrf_token: str
) -> None:
    """Validate CSRF token. Raises HTTPException(403) on failure."""
    if not session_cookie:
        raise HTTPException(status_code=403, detail="No admin session")
    if not mgr.verify_csrf_token(session_cookie, csrf_token):
        raise HTTPException(status_code=403, detail="Invalid or expired CSRF token")
```

For each state-changing POST endpoint, add `_csrf_token: str = Form(...)` to the handler signature and call `_verify_csrf`. Example (cache flush):

```python
@router.post("/admin/cache/flush")
async def cache_flush(
    request: Request,
    _csrf_token: str = Form(...),
    admin_session: str | None = Cookie(default=None),
):
    mgr = _require_admin_session(request, admin_session)
    if mgr is None:
        return RedirectResponse(url="/admin/login", status_code=303)

    # CSRF check
    _verify_csrf(mgr, admin_session, _csrf_token)

    # ... existing logic continues ...
```

Apply this same pattern to all POST endpoints that modify state:
- `/admin/cache/flush`
- `/admin/cache/invalidate`
- `/admin/keys/create`
- `/admin/keys/revoke/{key_id}`
- `/admin/sessions/create`
- `/admin/sessions/invalidate`
- `/admin/config/reload`

- [ ] **Step 4: Run existing tests to verify no regressions**

```bash
source .venv/bin/activate && pytest tests/unit/ -x -q
```

- [ ] **Step 5: Commit**

```bash
git add src/serp_llm/admin_session.py src/serp_llm/routes/admin_ui.py src/serp_llm/templates/
git commit -m "fix(admin): add CSRF token validation to admin UI POST endpoints"
```

---

### Task 3: Add security headers middleware

**Files:**
- Create: `src/serp_llm/middleware/__init__.py`
- Create: `src/serp_llm/middleware/security_headers.py`
- Modify: `src/serp_llm/main.py` (wire middleware)

- [ ] **Step 1: Create middleware package**

```bash
mkdir -p src/serp_llm/middleware
```

Create `src/serp_llm/middleware/__init__.py`:

```python
"""Middleware: rate limiting, security headers, and other per-request processing."""
```

Create `src/serp_llm/middleware/security_headers.py`:

```python
"""Middleware that sets security-related HTTP response headers.

Applies sensible defaults for HSTS, CSP, X-Frame-Options, and other
headers that protect against common web attacks.
"""

from __future__ import annotations

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to every response.

    Headers set:
    - Strict-Transport-Security (HSTS) — 1 year, include subdomains
    - X-Content-Type-Options — nosniff
    - X-Frame-Options — DENY (prevents clickjacking)
    - Referrer-Policy — no-referrer
    - Permissions-Policy — restrict sensitive browser features
    - Content-Security-Policy — base restrictive policy (relaxed for admin UI)
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)

        # HSTS — only set on HTTPS responses
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )

        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"

        # Permissions-Policy: disable features the admin UI doesn't need
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), interest-cohort=()"
        )

        # CSP: restrictive by default, relaxed for admin UI paths
        if request.url.path.startswith("/admin"):
            csp = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' https://unpkg.com/htmx.org; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; "
                "connect-src 'self'; "
                "form-action 'self'"
            )
        else:
            csp = (
                "default-src 'self'; "
                "script-src 'none'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self'; "
                "form-action 'none'"
            )
        response.headers["Content-Security-Policy"] = csp

        return response
```

- [ ] **Step 2: Write tests for security headers**

Create `tests/unit/test_security_headers.py`:

```python
"""Tests for security headers middleware."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from serp_llm.middleware.security_headers import SecurityHeadersMiddleware


def _make_app() -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.post("/search")
    async def search():
        return {"results": []}

    @app.get("/admin/dashboard")
    async def admin_dashboard():
        return {"admin": True}

    app.add_middleware(SecurityHeadersMiddleware)
    return app


class TestSecurityHeaders:
    """Security headers should be present on all responses."""

    def _check_common_headers(self, resp):
        assert resp.headers.get("x-content-type-options") == "nosniff"
        assert resp.headers.get("x-frame-options") == "DENY"
        assert resp.headers.get("referrer-policy") == "no-referrer"
        assert "permissions-policy" in resp.headers

    def test_api_endpoint_has_headers(self):
        client = TestClient(_make_app())
        resp = client.get("/health")
        self._check_common_headers(resp)

    def test_search_endpoint_has_headers(self):
        client = TestClient(_make_app())
        resp = client.post("/search", json={"query": "test"})
        self._check_common_headers(resp)

    def test_api_endpoint_has_restrictive_csp(self):
        client = TestClient(_make_app())
        resp = client.get("/health")
        csp = resp.headers.get("content-security-policy", "")
        assert "script-src 'none'" in csp, (
            f"Expected restrictive CSP on API endpoint, got: {csp}"
        )

    def test_admin_endpoint_has_relaxed_csp(self):
        client = TestClient(_make_app())
        resp = client.get("/admin/dashboard")
        csp = resp.headers.get("content-security-policy", "")
        assert "htmx.org" in csp, (
            f"Expected relaxed CSP (with htmx) on admin endpoint, got: {csp}"
        )

    def test_hsts_on_https(self):
        """HSTS header should only be set when the scheme is https."""
        # TestClient always uses http, so HSTS should NOT be set
        client = TestClient(_make_app())
        resp = client.get("/health")
        assert "strict-transport-security" not in resp.headers
```

- [ ] **Step 3: Wire security headers middleware in main.py**

In `src/serp_llm/main.py`, add the import:

```python
from serp_llm.middleware.security_headers import SecurityHeadersMiddleware
```

Add after the route inclusions (around line 232, after all `app.include_router(...)` calls and before exception handlers):

```python
    # --- Security headers ---
    app.add_middleware(SecurityHeadersMiddleware)
```

- [ ] **Step 4: Run security headers tests**

```bash
source .venv/bin/activate && pytest tests/unit/test_security_headers.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/serp_llm/middleware/ tests/unit/test_security_headers.py src/serp_llm/main.py
git commit -m "feat(security): add security headers middleware (HSTS, CSP, XFO, etc.)"
```
