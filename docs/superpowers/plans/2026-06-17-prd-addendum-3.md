# PRD-addendum-3 Implementation Plan

> **For agentic workers:** Build items 25-40 from PRD-addendum-3.md. Implements SQLite key store, bootstrap key, admin UI, docs site, OpenAPI spec.

**Goal:** Implement all features from PRD-addendum-3.md: SQLite-backed API keys with bcrypt, bootstrap key, admin UI (Jinja2+HTMX+TailwindCSS), MkDocs docs site, and OpenAPI spec quality pass.

**Architecture:** Key store replaces config-file auth for runtime key operations; admin UI uses session cookies via httpOnly cookies; all served from the existing FastAPI process with zero new containers.

**Tech Stack:** bcrypt, Jinja2, HTMX (CDN), TailwindCSS (CDN), MkDocs+Material theme, itsdangerous (session cookies), FastAPI built-in OpenAPI/Swagger.

---

### Wave 1: Foundation — Dependencies + Key Store + Auth Changes

**Files:**
- Modify: `pyproject.toml` — add bcrypt, jinja2, itsdangerous, aiosqlite
- Create: `src/webgateway/key_store.py` — SQLite api_keys table, bcrypt hash/verify, CRUD
- Create: `src/webgateway/routes/keys.py` — GET/POST/DELETE key endpoints
- Modify: `src/webgateway/auth.py` — dual lookup (config keys + SQLite keys + bootstrap key)
- Modify: `src/webgateway/schemas.py` — add key CRUD schemas
- Modify: `src/webgateway/main.py` — wire KeyStore, keys router
- Modify: `.env.example` — add BOOTSTRAP_ADMIN_KEY
- Modify: `config.yaml` — keep existing auth keys for backward compat

- [ ] **Step 1: Add dependencies to pyproject.toml**
- [ ] **Step 2: Create KeyStore class with bcrypt**
- [ ] **Step 3: Add bootstrap key logic to auth.py**
- [ ] **Step 4: Create key CRUD schemas**
- [ ] **Step 5: Create key CRUD REST routes**
- [ ] **Step 6: Wire KeyStore and keys router into main.py**
- [ ] **Step 7: Update .env.example and config.yaml**

### Wave 2: Admin Session Auth + Login/Logout

**Files:**
- Create: `src/webgateway/admin_session.py` — SQLite store for admin session cookies
- Create: `src/webgateway/templates/base.html` — base layout
- Create: `src/webgateway/templates/login.html` — login form
- Create: `src/webgateway/routes/admin_ui.py` — login/logout routes + all admin pages
- Modify: `src/webgateway/main.py` — wire admin session store, templates

- [ ] **Step 1: Create AdminSessionStore class**
- [ ] **Step 2: Create Jinja2 templates directory + base template**
- [ ] **Step 3: Create login template**
- [ ] **Step 4: Create admin_ui.py with login/logout routes**
- [ ] **Step 5: Wire into main.py**

### Wave 3: Admin UI Pages

**Files:**
- Create: `src/webgateway/templates/dashboard.html`
- Create: `src/webgateway/templates/keys.html`
- Create: `src/webgateway/templates/providers.html`
- Create: `src/webgateway/templates/sessions.html`
- Create: `src/webgateway/templates/usage.html`
- Create: `src/webgateway/templates/logs.html`
- Create: `src/webgateway/templates/cache.html`
- Modify: `src/webgateway/routes/admin_ui.py` — add all page routes

- [ ] **Step 1: Create dashboard page + route**
- [ ] **Step 2: Create keys page + route**
- [ ] **Step 3: Create providers page + route**
- [ ] **Step 4: Create sessions page + route**
- [ ] **Step 5: Create usage page + route**
- [ ] **Step 6: Create logs page + route**
- [ ] **Step 7: Create cache page + route**

### Wave 4: OpenAPI Spec Quality + Swagger UI

**Files:**
- Modify: `src/webgateway/main.py` — add OpenAPI metadata, mount docs
- Modify: `src/webgateway/routes/search.py` — add summaries, descriptions, tags
- Modify: `src/webgateway/routes/extract.py` — add summaries, descriptions, tags
- Modify: `src/webgateway/routes/health.py` — add summaries, descriptions, tags
- Modify: `src/webgateway/routes/providers.py` — add summaries, descriptions, tags
- Modify: `src/webgateway/routes/admin.py` — add summaries, descriptions, tags
- Modify: `src/webgateway/routes/cache.py` — add summaries, descriptions, tags
- Modify: `src/webgateway/routes/sessions_admin.py` — add summaries, descriptions, tags

- [ ] **Step 1: Enhance FastAPI app creation with OpenAPI metadata**
- [ ] **Step 2: Add summaries/descriptions/tags to all existing route handlers**

### Wave 5: MkDocs Docs Site

**Files:**
- Create: `docs-src/mkdocs.yml`
- Create: `docs-src/docs/getting-started/`
- Create: `docs-src/docs/configuration/`
- Create: `docs-src/docs/providers/`
- Create: `docs-src/docs/sessions/`
- Create: `docs-src/docs/operations/`
- Create: `docs-src/docs/api/`
- Create: `docs-src/docs/architecture/`
- Create: `scripts/generate_provider_pages.py`
- Modify: `Dockerfile` — add MkDocs build step
- Modify: `.dockerignore` or Dockerfile — add docs-src copy

- [ ] **Step 1: Create mkdocs.yml config**
- [ ] **Step 2: Create all doc content pages**
- [ ] **Step 3: Create provider data policy page generator script**
- [ ] **Step 4: Update Dockerfile to build MkDocs**

### Wave 6: Tests

**Files:**
- Create: `tests/unit/test_key_store.py`
- Create: `tests/unit/test_admin_session.py`
- Create: `tests/unit/test_admin_ui.py`

- [ ] **Step 1: Test KeyStore CRUD + bcrypt**
- [ ] **Step 2: Test admin session store**
- [ ] **Step 3: Test admin login/logout flow**
