# Stealth Browser + Cookie Bucket Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add invisible_playwright stealth browser provider adapter + Fernet-encrypted session store with admin UI to WebGateway.

**Architecture:** New `src/webgateway/sessions/` module handles session CRUD and validation. New `src/webgateway/providers/invisible_playwright.py` adapter calls the browser sidecar REST API. GatewayService gains session resolution and cache-bypass logic. Admin routes expose session CRUD. All new fields on existing config/schema models are optional with safe defaults.

**Tech Stack:** Python 3.12+, FastAPI, Pydantic v2, httpx, Fernet (cryptography), pytest

**Dependency order:** Tasks 1-2 → Tasks 3,5,7,9 → Tasks 4,6,8,10 → Tasks 11-12 → Task 13 → Task 14. Tasks within each batch can run in parallel.

---

### Task 1: Config model extensions

**Files:**
- Modify: `src/webgateway/config.py:96-103` (ProviderConfig)
- Modify: `src/webgateway/config.py:176-179` (SessionsConfig)
- Modify: `src/webgateway/config.py:250-264` (GatewayConfig — add StealthConfig)

- [ ] **Step 1: Add StealthConfig model and extend ProviderConfig/SessionsConfig**

Edit `src/webgateway/config.py`:

Add `Literal` import (already imported at top of file).

After `SessionsConfig` class (line ~179), add `StealthConfig`:

```python
class StealthConfig(BaseModel):
    fingerprint_rotation:
        same_domain_window_seconds: int = 3600
        pool_size: int = 10
```

Wait, Pydantic v2 uses this syntax:

```python
class StealthConfig(BaseModel):
    fingerprint_rotation: dict[str, int] = Field(
        default_factory=lambda: {"same_domain_window_seconds": 3600, "pool_size": 10}
    )
```

Actually looking at the existing patterns in config.py, fields use simple declarations. Let me match the style:

```python
class FingerprintRotationConfig(BaseModel):
    same_domain_window_seconds: int = 3600
    pool_size: int = 10


class StealthConfig(BaseModel):
    fingerprint_rotation: FingerprintRotationConfig = Field(
        default_factory=FingerprintRotationConfig
    )
```

Extend `ProviderConfig` (add after line 103 `health_check_enabled: bool = True`):

```python
    stealth: bool = False
    engine: str | None = None
    firefox_version: str | None = None
    specialization: str | None = None
    warnings: list[str] = Field(default_factory=list)
    cost_units_per_call: float = 1.0
```

Extend `SessionsConfig` (replace existing):

```python
class SessionsConfig(BaseModel):
    store_path: str = "/app/sessions"
    encryption_key: str | None = None
    auto_invalidate_on_login_wall: bool = True
    strict_proxy_binding: bool = True
    login_wall_patterns: list[str] = Field(
        default_factory=lambda: [
            "Sign in",
            "Log in to continue",
            "Subscribe to read",
            "Create an account",
            "Your session has expired",
            "Please log in",
            "Access restricted",
        ]
    )
```

Add `StealthConfig` field to `GatewayConfig` (after `sessions` line):

```python
    stealth: StealthConfig = Field(default_factory=StealthConfig)
```

- [ ] **Step 2: Verify config loads with new fields**

Run: `python -c "from webgateway.config import GatewayConfig; cfg = GatewayConfig(); print('stealth:', cfg.stealth.model_dump()); print('sessions:', cfg.sessions.model_dump())"`
Expected: prints default values for both config sections

- [ ] **Step 3: Run existing tests to confirm no regressions**

Run: `source .venv/bin/activate && pytest tests/unit/ -v --tb=short 2>&1 | tail -20`
Expected: all existing tests pass

- [ ] **Step 4: Commit**

```bash
git add src/webgateway/config.py
git commit -m "feat: add StealthConfig, extend ProviderConfig and SessionsConfig"
```

---

### Task 2: Base model extensions (ExtractOptions + ProviderMetadata)

**Files:**
- Modify: `src/webgateway/providers/base.py:26-31` (ExtractOptions)
- Modify: `src/webgateway/providers/base.py:66-78` (ProviderMetadata)

- [ ] **Step 1: Add fields to ExtractOptions**

Edit `src/webgateway/providers/base.py`. Add `session_id`, `fingerprint_id`, `user_agent` to `ExtractOptions`:

```python
@dataclass
class ExtractOptions:
    format: str = "markdown"
    proxy_url: str | None = None
    wait_for_selector: str | None = None
    session_cookies: dict[str, str] | None = None
    session_id: str | None = None
    fingerprint_id: str | None = None
    user_agent: str | None = None
    timeout: int = 15
```

- [ ] **Step 2: Add fields to ProviderMetadata**

Edit `src/webgateway/providers/base.py`. Add after `capabilities` in `ProviderMetadata`:

```python
    warnings: list[str] = field(default_factory=list)
    stealth: bool = False
    engine: str | None = None
    firefox_version: str | None = None
    specialization: str | None = None
    cost_units_per_call: float = 1.0
```

- [ ] **Step 3: Verify imports still work**

Run: `source .venv/bin/activate && python -c "from webgateway.providers.base import ExtractOptions, ProviderMetadata; e = ExtractOptions(); p = ProviderMetadata(name='test'); print('OK:', e.session_id, p.warnings)"`
Expected: prints `OK: None []`

- [ ] **Step 4: Commit**

```bash
git add src/webgateway/providers/base.py
git commit -m "feat: add session/fingerprint fields to ExtractOptions and ProviderMetadata"
```

---

### Task 3: Session models

**Files:**
- Create: `src/webgateway/sessions/__init__.py`
- Create: `src/webgateway/sessions/models.py`

- [ ] **Step 1: Create package init**

Write `src/webgateway/sessions/__init__.py`:

```python
"""Encrypted session store for authenticated browser sessions."""
```

- [ ] **Step 2: Create session data models**

Write `src/webgateway/sessions/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field


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
    browser_service: str
    domain: str
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
    """Public metadata — no cookie values or local_storage."""

    session_id: str
    domain: str
    browser: str
    engine: str
    created_ts: float
    last_used_ts: float
    expiry: float | None = None
    proxy_binding: str | None = None
    strict_proxy: bool = False
    cookie_count: int = 0
    use_count: int = 0


def session_to_info(data: SessionData) -> SessionInfo:
    """Convert full SessionData to public SessionInfo (strips secrets)."""
    return SessionInfo(
        session_id=data.session_id,
        domain=data.domain,
        browser=data.browser_service,
        engine="firefox",
        created_ts=data.created_ts,
        last_used_ts=data.last_used_ts,
        expiry=data.expiry_ts,
        proxy_binding=data.proxy_binding,
        strict_proxy=data.strict_proxy,
        cookie_count=len(data.cookies),
        use_count=data.use_count,
    )
```

- [ ] **Step 3: Verify module imports**

Run: `source .venv/bin/activate && python -c "from webgateway.sessions.models import SessionData, CookieEntry, session_to_info; print('OK')"`
Expected: prints `OK`

- [ ] **Step 4: Commit**

```bash
git add src/webgateway/sessions/__init__.py src/webgateway/sessions/models.py
git commit -m "feat: add session data models"
```

---

### Task 4: Session store (Fernet-encrypted file CRUD)

**Files:**
- Create: `src/webgateway/sessions/store.py`
- Create: `tests/unit/__init__.py` (if missing)
- Create: `tests/unit/test_session_store.py`

- [ ] **Step 1: Write failing test**

Write `tests/unit/test_session_store.py`:

```python
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from webgateway.sessions.models import CookieEntry, SessionData, session_to_info
from webgateway.sessions.store import SessionNotFound, SessionStore


@pytest.fixture
def key() -> str:
    return Fernet.generate_key().decode()


@pytest.fixture
def store_path(tmp_path: Path) -> str:
    return str(tmp_path / "sessions")


@pytest.fixture
def store(store_path: str, key: str) -> SessionStore:
    return SessionStore(store_path, key)


@pytest.fixture
def sample_session() -> SessionData:
    return SessionData(
        session_id="test_session_001",
        browser_service="invisible_playwright",
        domain="example.com",
        cookies=[
            CookieEntry(name="sessionid", value="abc123", domain="example.com"),
        ],
        user_agent="Mozilla/5.0 Firefox/150.0",
        fingerprint_id="fp_01",
        created_ts=time.time(),
        last_used_ts=time.time(),
        expiry_ts=time.time() + 86400,
    )


class TestSessionStore:
    async def test_save_and_load(self, store: SessionStore, sample_session: SessionData):
        store.save(sample_session)
        loaded = store.load(sample_session.session_id)
        assert loaded.session_id == sample_session.session_id
        assert loaded.domain == sample_session.domain
        assert loaded.cookies[0].name == "sessionid"
        assert loaded.cookies[0].value == "abc123"

    async def test_load_nonexistent_raises(self, store: SessionStore):
        with pytest.raises(SessionNotFound):
            store.load("nonexistent")

    async def test_delete_removes_file(self, store: SessionStore, sample_session: SessionData):
        store.save(sample_session)
        assert store.exists(sample_session.session_id)
        store.delete(sample_session.session_id)
        assert not store.exists(sample_session.session_id)

    async def test_delete_missing_is_noop(self, store: SessionStore):
        store.delete("nonexistent")  # should not raise

    async def test_list_sessions(self, store: SessionStore, sample_session: SessionData):
        store.save(sample_session)
        sessions = store.list_sessions()
        assert len(sessions) == 1
        info = sessions[0]
        assert info.session_id == "test_session_001"
        assert info.cookie_count == 1
        assert info.browser == "invisible_playwright"

    async def test_corrupted_file_raises(self, store: SessionStore, store_path: str):
        enc_path = Path(store_path) / "corrupt.enc"
        enc_path.parent.mkdir(parents=True, exist_ok=True)
        enc_path.write_text("not valid fernet data")
        with pytest.raises(Exception):
            store.load("corrupt")

    async def test_wrong_key_raises(self, store_path: str, sample_session: SessionData):
        key1 = Fernet.generate_key().decode()
        store1 = SessionStore(store_path, key1)
        store1.save(sample_session)

        key2 = Fernet.generate_key().decode()
        store2 = SessionStore(store_path, key2)
        with pytest.raises(Exception):
            store2.load(sample_session.session_id)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && pytest tests/unit/test_session_store.py -v --tb=short 2>&1 | head -30`
Expected: ImportError for `SessionStore`

- [ ] **Step 3: Implement SessionStore**

Write `src/webgateway/sessions/store.py`:

```python
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from webgateway.sessions.models import (
    CookieEntry,
    SessionData,
    SessionInfo,
    session_to_info,
)


class SessionNotFound(KeyError):
    """Raised when a session file does not exist."""


class SessionStore:
    """Fernet-encrypted session file store. One file per session."""

    def __init__(self, store_path: str, encryption_key: str) -> None:
        self._store_dir = Path(store_path)
        self._store_dir.mkdir(parents=True, exist_ok=True)
        self._fernet = Fernet(encryption_key.encode() if isinstance(encryption_key, str) else encryption_key)

    def _path(self, session_id: str) -> Path:
        return self._store_dir / f"{session_id}.enc"

    def save(self, session: SessionData) -> None:
        """Encrypt and write session file."""
        raw = _session_to_json(session)
        encrypted = self._fernet.encrypt(raw.encode())
        self._path(session.session_id).write_bytes(encrypted)

    def load(self, session_id: str) -> SessionData:
        """Read and decrypt session file."""
        path = self._path(session_id)
        if not path.exists():
            raise SessionNotFound(session_id)
        try:
            encrypted = path.read_bytes()
            decrypted = self._fernet.decrypt(encrypted)
            return _json_to_session(decrypted.decode())
        except (InvalidToken, json.JSONDecodeError, KeyError) as exc:
            raise ValueError(
                f"Failed to decrypt/parse session {session_id!r}: {exc}"
            ) from exc

    def delete(self, session_id: str) -> None:
        """Remove session file. No-op if missing."""
        path = self._path(session_id)
        if path.exists():
            path.unlink()

    def list_sessions(self) -> list[SessionInfo]:
        """Iterate all .enc files, decrypt and return metadata only."""
        results: list[SessionInfo] = []
        for enc_path in sorted(self._store_dir.glob("*.enc")):
            session_id = enc_path.stem
            try:
                data = self.load(session_id)
                results.append(session_to_info(data))
            except Exception:
                continue
        return results

    def exists(self, session_id: str) -> bool:
        return self._path(session_id).exists()


def _session_to_json(session: SessionData) -> str:
    """Serialize SessionData to JSON string."""
    return json.dumps({
        "session_id": session.session_id,
        "browser_service": session.browser_service,
        "domain": session.domain,
        "cookies": [
            {
                "name": c.name,
                "value": c.value,
                "domain": c.domain,
                "path": c.path,
                "expiry": c.expiry,
                "secure": c.secure,
                "http_only": c.http_only,
            }
            for c in session.cookies
        ],
        "user_agent": session.user_agent,
        "fingerprint_id": session.fingerprint_id,
        "created_ts": session.created_ts,
        "last_used_ts": session.last_used_ts,
        "expiry_ts": session.expiry_ts,
        "proxy_binding": session.proxy_binding,
        "strict_proxy": session.strict_proxy,
        "use_count": session.use_count,
        "local_storage": session.local_storage,
    }, default=str)


def _json_to_session(raw: str) -> SessionData:
    """Deserialize JSON string to SessionData."""
    data = json.loads(raw)
    cookies = [CookieEntry(**c) for c in data.get("cookies", [])]
    return SessionData(
        session_id=data["session_id"],
        browser_service=data["browser_service"],
        domain=data["domain"],
        cookies=cookies,
        user_agent=data["user_agent"],
        fingerprint_id=data["fingerprint_id"],
        created_ts=data["created_ts"],
        last_used_ts=data["last_used_ts"],
        expiry_ts=data.get("expiry_ts"),
        proxy_binding=data.get("proxy_binding"),
        strict_proxy=data.get("strict_proxy", False),
        use_count=data.get("use_count", 0),
        local_storage=data.get("local_storage"),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && pytest tests/unit/test_session_store.py -v --tb=short 2>&1`
Expected: all 7 tests pass

- [ ] **Step 5: Commit**

```bash
git add src/webgateway/sessions/store.py tests/unit/test_session_store.py
git commit -m "feat: add Fernet-encrypted SessionStore"
```

---

### Task 5: Session schemas for admin API

**Files:**
- Modify: `src/webgateway/schemas.py` (add session Pydantic models)

- [ ] **Step 1: Add session schemas to schemas.py**

Append to `src/webgateway/schemas.py` before the final newline:

```python
# ---------------------------------------------------------------------------
# Session / Cookie Bucket admin schemas
# ---------------------------------------------------------------------------


class CookieEntrySchema(BaseModel):
    name: str
    value: str
    domain: str
    path: str = "/"
    expiry: float | None = None
    secure: bool = True
    http_only: bool = True


class SessionCreateRequest(BaseModel):
    session_id: str
    browser: str = "invisible_playwright"
    domain: str
    cookies: list[CookieEntrySchema]
    user_agent: str
    fingerprint_id: str
    expiry: datetime | None = None
    proxy_binding: str | None = None
    strict_proxy: bool = False


class SessionInfoResponse(BaseModel):
    session_id: str
    domain: str
    browser: str
    engine: str = "firefox"
    created_ts: float
    last_used_ts: float
    expiry: float | None = None
    proxy_binding: str | None = None
    strict_proxy: bool = False
    cookie_count: int = 0
    use_count: int = 0


class SessionStatusResponse(BaseModel):
    session_id: str
    valid: bool
    expired: bool = False
    domain_bound: str | None = None
    browser: str | None = None
    fingerprint_id: str | None = None
    last_used_ts: float | None = None
    use_count: int = 0
    proxy_binding: str | None = None


class SessionInvalidateRequest(BaseModel):
    session_id: str | None = None
    domain: str | None = None
    browser: str | None = None


class SessionRefreshRequest(BaseModel):
    cookies: list[CookieEntrySchema]


class SessionErrorResponse(BaseModel):
    error: str
    error_class: str
    session_id: str | None = None
    message: str
```

Add `from datetime import datetime` to the existing imports at the top of the file (it's already imported? Let me check... looking at the existing schemas.py, there's no datetime import. I need to add it.)

Actually let me check — the existing schemas use `str | None` not datetime. But `SessionCreateRequest` has `expiry: datetime | None`. I'll need to add the import.

- [ ] **Step 2: Verify schemas load**

Run: `source .venv/bin/activate && python -c "from webgateway.schemas import SessionCreateRequest, SessionInfoResponse, SessionStatusResponse; print('OK')"`
Expected: prints `OK`

- [ ] **Step 3: Commit**

```bash
git add src/webgateway/schemas.py
git commit -m "feat: add session admin API schemas"
```

---

### Task 6: Session error + manager

**Files:**
- Create: `src/webgateway/sessions/manager.py`
- Create: `tests/unit/test_session_manager.py`

- [ ] **Step 1: Write failing test for SessionManager**

Write `tests/unit/test_session_manager.py`:

```python
from __future__ import annotations

import time
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from webgateway.sessions.manager import SessionError, SessionManager
from webgateway.sessions.models import CookieEntry, SessionData
from webgateway.sessions.store import SessionStore


@pytest.fixture
def store_path(tmp_path: Path) -> str:
    return str(tmp_path / "sessions")


@pytest.fixture
def key() -> str:
    return Fernet.generate_key().decode()


@pytest.fixture
def store(store_path: str, key: str) -> SessionStore:
    return SessionStore(store_path, key)


@pytest.fixture
def manager(store: SessionStore) -> SessionManager:
    from webgateway.config import SessionsConfig
    return SessionManager(store, SessionsConfig())


def _make_session(session_id: str = "sess_001", **overrides) -> SessionData:
    defaults = dict(
        session_id=session_id,
        browser_service="invisible_playwright",
        domain="example.com",
        cookies=[CookieEntry(name="sid", value="abc", domain="example.com")],
        user_agent="Mozilla/5.0 Firefox/150.0",
        fingerprint_id="fp_01",
        created_ts=time.time(),
        last_used_ts=time.time(),
        expiry_ts=time.time() + 86400,
    )
    defaults.update(overrides)
    return SessionData(**defaults)


class TestSessionManager:
    async def test_resolve_valid(self, manager: SessionManager, store: SessionStore):
        session = _make_session()
        store.save(session)
        resolved = await manager.resolve(
            "sess_001",
            provider_name="invisible_playwright",
            domain="example.com",
            proxy_name=None,
        )
        assert resolved.session_id == "sess_001"

    async def test_resolve_expired_raises(self, manager: SessionManager, store: SessionStore):
        session = _make_session(expiry_ts=time.time() - 1)
        store.save(session)
        with pytest.raises(SessionError) as exc:
            await manager.resolve(
                "sess_001",
                provider_name="invisible_playwright",
                domain="example.com",
                proxy_name=None,
            )
        assert exc.value.error_class == "session_expired"

    async def test_resolve_browser_mismatch_raises(self, manager: SessionManager, store: SessionStore):
        session = _make_session(browser_service="camoufox")
        store.save(session)
        with pytest.raises(SessionError) as exc:
            await manager.resolve(
                "sess_001",
                provider_name="invisible_playwright",
                domain="example.com",
                proxy_name=None,
            )
        assert exc.value.error_class == "session_browser_mismatch"

    async def test_resolve_domain_mismatch_raises(self, manager: SessionManager, store: SessionStore):
        session = _make_session(domain="other.com")
        store.save(session)
        with pytest.raises(SessionError) as exc:
            await manager.resolve(
                "sess_001",
                provider_name="invisible_playwright",
                domain="example.com",
                proxy_name=None,
            )
        assert exc.value.error_class == "session_domain_mismatch"

    async def test_resolve_proxy_mismatch_raises(self, manager: SessionManager, store: SessionStore):
        session = _make_session(proxy_binding="residential_us", strict_proxy=True)
        store.save(session)
        with pytest.raises(SessionError) as exc:
            await manager.resolve(
                "sess_001",
                provider_name="invisible_playwright",
                domain="example.com",
                proxy_name="different_proxy",
            )
        assert exc.value.error_class == "session_proxy_mismatch"

    async def test_touch_updates_ts(self, manager: SessionManager, store: SessionStore):
        session = _make_session(use_count=0)
        store.save(session)
        await manager.touch("sess_001")
        loaded = store.load("sess_001")
        assert loaded.use_count == 1

    async def test_invalidate_by_id(self, manager: SessionManager, store: SessionStore):
        store.save(_make_session("sess_a"))
        store.save(_make_session("sess_b"))
        count = await manager.invalidate(session_id="sess_a")
        assert count == 1
        assert not store.exists("sess_a")
        assert store.exists("sess_b")

    async def test_invalidate_by_domain(self, manager: SessionManager, store: SessionStore):
        store.save(_make_session("sess_a", domain="example.com"))
        store.save(_make_session("sess_b", domain="other.com"))
        count = await manager.invalidate(domain="example.com")
        assert count == 1
        assert not store.exists("sess_a")
        assert store.exists("sess_b")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && pytest tests/unit/test_session_manager.py -v --tb=short 2>&1 | head -20`
Expected: ImportError for SessionManager

- [ ] **Step 3: Implement SessionManager**

Write `src/webgateway/sessions/manager.py`:

```python
from __future__ import annotations

import time
import fnmatch
from urllib.parse import urlparse

from webgateway.sessions.models import SessionData, SessionInfo
from webgateway.sessions.store import SessionNotFound, SessionStore


class SessionError(Exception):
    """Raised when a session cannot be resolved or is invalid.

    Attributes:
        error_class: Machine-readable error type string.
        session_id: The session ID that caused the error, if applicable.
    """

    def __init__(
        self,
        error_class: str,
        message: str,
        *,
        session_id: str | None = None,
    ) -> None:
        self.error_class = error_class
        self.session_id = session_id
        super().__init__(f"[{error_class}] {message}")


class SessionManager:
    """Session lifecycle management — wraps store with validation."""

    def __init__(
        self,
        store: SessionStore,
        config: object,  # SessionsConfig — forward-ref to avoid circular import
    ) -> None:
        self._store = store
        self._config = config

    async def resolve(
        self,
        session_id: str,
        *,
        provider_name: str,
        domain: str,
        proxy_name: str | None,
    ) -> SessionData:
        """Load session, validate all bindings."""
        try:
            session = self._store.load(session_id)
        except SessionNotFound:
            raise SessionError(
                "session_not_found",
                f"Session {session_id!r} not found",
                session_id=session_id,
            )

        now = time.time()

        # Expiry check
        if session.expiry_ts is not None and now > session.expiry_ts:
            self._store.delete(session_id)
            raise SessionError(
                "session_expired",
                f"Session {session_id!r} expired",
                session_id=session_id,
            )

        # Browser service check
        if session.browser_service != provider_name:
            raise SessionError(
                "session_browser_mismatch",
                f"Session {session_id!r} is bound to {session.browser_service!r}, "
                f"but request uses {provider_name!r}",
                session_id=session_id,
            )

        # Domain check (exact or subdomain match)
        if not _domain_matches(session.domain, domain):
            raise SessionError(
                "session_domain_mismatch",
                f"Session {session_id!r} is bound to domain {session.domain!r}, "
                f"but request domain is {domain!r}",
                session_id=session_id,
            )

        # Strict proxy check
        if (
            session.strict_proxy
            and session.proxy_binding is not None
            and proxy_name != session.proxy_binding
        ):
            raise SessionError(
                "session_proxy_mismatch",
                f"Session {session_id!r} requires proxy {session.proxy_binding!r}, "
                f"but request resolves to {proxy_name!r}",
                session_id=session_id,
            )

        # Update last_used_ts + use_count in background
        session.last_used_ts = now
        session.use_count += 1
        self._store.save(session)

        return session

    async def invalidate(
        self,
        *,
        session_id: str | None = None,
        domain: str | None = None,
        browser: str | None = None,
    ) -> int:
        """Invalidate matching sessions. Returns count of invalidated sessions."""
        if session_id is not None:
            self._store.delete(session_id)
            return 1

        count = 0
        for info in self._store.list_sessions():
            match = True
            if domain is not None and not _domain_matches(info.domain, domain):
                match = False
            if browser is not None and info.browser != browser:
                match = False
            if match:
                self._store.delete(info.session_id)
                count += 1
        return count

    async def touch(self, session_id: str) -> None:
        """Update last_used_ts and increment use_count."""
        try:
            session = self._store.load(session_id)
        except SessionNotFound:
            return
        session.last_used_ts = time.time()
        session.use_count += 1
        self._store.save(session)


def _domain_matches(session_domain: str, request_domain: str) -> bool:
    """Check if *request_domain* matches *session_domain* (exact or subdomain).

    A session bound to "wsj.com" matches "www.wsj.com" and "wsj.com".
    """
    if session_domain == request_domain:
        return True
    if request_domain.endswith("." + session_domain):
        return True
    return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && pytest tests/unit/test_session_manager.py -v --tb=short 2>&1`
Expected: all tests pass

- [ ] **Step 5: Commit**

```bash
git add src/webgateway/sessions/manager.py tests/unit/test_session_manager.py
git commit -m "feat: add SessionManager with validation logic"
```

---

### Task 7: InvisiblePlaywright provider adapter

**Files:**
- Create: `src/webgateway/providers/invisible_playwright.py`
- Create: `tests/unit/test_invisible_playwright.py`

- [ ] **Step 1: Write failing test**

Write `tests/unit/test_invisible_playwright.py`:

```python
from __future__ import annotations

import httpx
import pytest
from pytest_httpx import HTTPXMock

from webgateway.providers.base import ExtractOptions, ProviderError, ProviderMetadata
from webgateway.providers.invisible_playwright import InvisiblePlaywrightAdapter


@pytest.fixture
def adapter() -> InvisiblePlaywrightAdapter:
    return InvisiblePlaywrightAdapter(
        base_url="http://invisible-playwright:3001",
        timeout=15,
    )


class TestInvisiblePlaywrightAdapter:
    async def test_name(self, adapter: InvisiblePlaywrightAdapter):
        assert adapter.name == "invisible_playwright"

    async def test_metadata(self, adapter: InvisiblePlaywrightAdapter):
        meta = adapter.metadata
        assert isinstance(meta, ProviderMetadata)
        assert meta.name == "invisible_playwright"
        assert meta.self_hosted is True
        assert meta.stealth is True
        assert meta.engine == "firefox"
        assert "extract" in meta.capabilities
        assert "search" not in meta.capabilities

    async def test_search_raises_not_supported(self, adapter: InvisiblePlaywrightAdapter):
        with pytest.raises(ProviderError) as exc:
            await adapter.search("test query", ExtractOptions())
        assert exc.value.error_class == "not_supported"

    async def test_extract_success(self, adapter: InvisiblePlaywrightAdapter, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            method="POST",
            url="http://invisible-playwright:3001/scrape",
            json={
                "content": "# Hello World\n\nThis is the article.",
                "format": "markdown",
                "url": "https://example.com/article",
                "title": "Hello World Article",
            },
        )
        result = await adapter.extract(
            "https://example.com/article",
            ExtractOptions(),
        )
        assert result.content == "# Hello World\n\nThis is the article."
        assert result.format == "markdown"
        assert result.title == "Hello World Article"

    async def test_extract_with_session(self, adapter: InvisiblePlaywrightAdapter, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            method="POST",
            url="http://invisible-playwright:3001/scrape",
            json={"content": "auth content", "format": "markdown", "url": "https://wsj.com/article"},
        )
        result = await adapter.extract(
            "https://wsj.com/article",
            ExtractOptions(
                session_id="wsj_session_abc",
                session_cookies={"sessionid": "xyz"},
                fingerprint_id="fp_03",
                user_agent="Mozilla/5.0 Firefox/150.0",
                proxy_url="http://residential:24000",
                wait_for_selector=".article-body",
            ),
        )
        assert result.content == "auth content"
        # Verify the request body
        request = httpx_mock.get_request()
        body = request.json()
        assert body["session_id"] == "wsj_session_abc"
        assert body["fingerprint"] == "fp_03"
        assert body["wait_for_selector"] == ".article-body"

    async def test_extract_http_error(self, adapter: InvisiblePlaywrightAdapter, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            method="POST",
            url="http://invisible-playwright:3001/scrape",
            status_code=500,
        )
        with pytest.raises(ProviderError) as exc:
            await adapter.extract("https://example.com/article", ExtractOptions())
        assert exc.value.status_code == 500

    async def test_health_check_success(self, adapter: InvisiblePlaywrightAdapter, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            method="GET",
            url="http://invisible-playwright:3001/health",
            status_code=200,
        )
        assert await adapter.health_check() is True

    async def test_health_check_failure(self, adapter: InvisiblePlaywrightAdapter, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            method="GET",
            url="http://invisible-playwright:3001/health",
            status_code=503,
        )
        assert await adapter.health_check() is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && pip install pytest-httpx && pytest tests/unit/test_invisible_playwright.py -v --tb=short 2>&1 | head -20`
Expected: ImportError for InvisiblePlaywrightAdapter

- [ ] **Step 3: Implement InvisiblePlaywrightAdapter**

Write `src/webgateway/providers/invisible_playwright.py`:

```python
from __future__ import annotations

import httpx

from webgateway.providers.base import (
    ExtractOptions,
    ExtractResult,
    ProviderError,
    ProviderMetadata,
    ResultItem,
    SearchOptions,
    SearchResult,
)


class InvisiblePlaywrightAdapter:
    """Adapter for the invisible_playwright REST sidecar.

    The sidecar runs a C++-patched Firefox 150 that is undetectable by
    Cloudflare, DataDome, and reCAPTCHA v3 fingerprinting.  This adapter
    only supports ``extract()`` — the stealth browser is not a search API.
    """

    def __init__(
        self,
        base_url: str = "http://invisible-playwright:3001",
        timeout: int = 15,
        *,
        warnings: list[str] | None = None,
        firefox_version: str = "150",
        cost_units_per_call: float = 0.8,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._warnings = warnings or []
        self._firefox_version = firefox_version
        self._cost_units_per_call = cost_units_per_call

    # ------------------------------------------------------------------
    # ProviderAdapter protocol
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "invisible_playwright"

    @property
    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            name=self.name,
            self_hosted=True,
            data_retention_days=0,
            trains_on_queries=False,
            gdpr_compliant=True,
            hipaa_compliant=False,
            data_residency=["local"],
            capabilities=["extract"],
            warnings=list(self._warnings),
            stealth=True,
            engine="firefox",
            firefox_version=self._firefox_version,
            specialization="stealth_primary",
            cost_units_per_call=self._cost_units_per_call,
        )

    async def search(self, query: str, options: SearchOptions) -> SearchResult:
        raise ProviderError(
            self.name,
            "Stealth browser does not support search — use extract() instead",
            error_class="not_supported",
        )

    async def extract(self, url: str, options: ExtractOptions) -> ExtractResult:
        """Scrape *url* via the invisible_playwright sidecar.

        Session cookies, fingerprint, proxy, and UA are passed through
        when provided in *options*.
        """
        payload: dict[str, object] = {
            "url": url,
            "timeout": int((options.timeout or self._timeout) * 1000),
        }

        if options.proxy_url:
            payload["proxy"] = options.proxy_url

        if options.fingerprint_id:
            payload["fingerprint"] = options.fingerprint_id
        elif options.session_id:
            payload["fingerprint"] = "rotate"

        if options.session_id:
            payload["session_id"] = options.session_id

        if options.session_cookies:
            payload["cookies"] = [
                {"name": k, "value": v}
                for k, v in options.session_cookies.items()
            ]

        if options.user_agent:
            payload["user_agent"] = options.user_agent

        if options.wait_for_selector:
            payload["wait_for_selector"] = options.wait_for_selector

        try:
            async with httpx.AsyncClient(
                timeout=options.timeout + 30 if options.timeout else self._timeout + 30,
            ) as client:
                resp = await client.post(
                    f"{self._base_url}/scrape",
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise ProviderError(
                self.name, f"Request failed: {exc}"
            ) from exc

        if resp.status_code >= 400:
            raise ProviderError(
                self.name,
                f"HTTP {resp.status_code}",
                status_code=resp.status_code,
            )

        data = resp.json()
        return ExtractResult(
            content=str(data.get("content", "")),
            format=data.get("format", "markdown"),
            url=str(data.get("url", url)),
            title=str(data.get("title")) if data.get("title") else None,
        )

    async def health_check(self) -> bool:
        """Check if the sidecar is reachable via its health endpoint."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self._base_url}/health")
                return resp.status_code < 400
        except httpx.HTTPError:
            return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && pytest tests/unit/test_invisible_playwright.py -v --tb=short 2>&1`
Expected: all tests pass

- [ ] **Step 5: Commit**

```bash
git add src/webgateway/providers/invisible_playwright.py tests/unit/test_invisible_playwright.py
git commit -m "feat: add invisible_playwright provider adapter"
```

---

### Task 8: Register invisible_playwright in provider registry

**Files:**
- Modify: `src/webgateway/providers/registry.py`

- [ ] **Step 1: Add import + registration to _create_adapter**

Edit `src/webgateway/providers/registry.py`:

Add import at the top (after line 19):

```python
from webgateway.providers.invisible_playwright import InvisiblePlaywrightAdapter
```

Add registration in `_create_adapter` (after the `firecrawl_selfhosted` block, before the `logger.warning` line):

```python
        if name == "invisible_playwright":
            return InvisiblePlaywrightAdapter(
                base_url=cfg.base_url or "http://invisible-playwright:3001",
                timeout=cfg.timeout or 15,
                warnings=cfg.warnings,
                firefox_version=cfg.firefox_version or "150",
                cost_units_per_call=cfg.cost_units_per_call or 0.8,
            )
```

- [ ] **Step 2: Verify adapter loads from registry**

Run: `source .venv/bin/activate && python -c "
from webgateway.config import ConfigManager
from webgateway.providers.registry import ProviderRegistry
cm = ConfigManager('config.yaml', autoload=True)
print('Configured providers:', list(cm.config.providers.keys()))
reg = ProviderRegistry(cm)
print('Registered:', reg.list_names())
if reg.has('invisible_playwright'):
    a = reg.get('invisible_playwright')
    print('Adapter metadata:', a.metadata)
"`
Expected: invisible_playwright shows in registered providers (or isn't if not in config.yaml yet — that's OK)

- [ ] **Step 3: Commit**

```bash
git add src/webgateway/providers/registry.py
git commit -m "feat: register invisible_playwright adapter in provider registry"
```

---

### Task 9: Extend audit entry with session fields

**Files:**
- Modify: `src/webgateway/audit.py`

- [ ] **Step 1: Add session fields to AuditEntry**

Edit `src/webgateway/audit.py`:

Add after `cache_invalidated: bool = False` (line ~60):

```python
    session_profile: str | None = None
    session_valid: bool | None = None
    session_expired: bool | None = None
    fingerprint_id: str | None = None
    browser_service: str | None = None
    browser_engine: str | None = None
    firefox_version: str | None = None
```

Also update the `type` literal to include future values... no, keep as `Literal["search", "extract"]` since session audit fields are just metadata fields on the same request types.

- [ ] **Step 2: Verify dataclass still works**

Run: `source .venv/bin/activate && python -c "from webgateway.audit import AuditEntry; e = AuditEntry(request_id='r1', api_key_id='k1', type='extract', url='u', provider_used='p', latency_ms=10, status='success', session_profile='sess_001'); print('OK:', e.session_profile)"`
Expected: prints `OK: sess_001`

- [ ] **Step 3: Commit**

```bash
git add src/webgateway/audit.py
git commit -m "feat: add session and browser fields to AuditEntry"
```

---

### Task 10: Provider warnings in GET /providers

**Files:**
- Modify: `src/webgateway/schemas.py:99-111` (ProviderMetadataInfo)
- Modify: `src/webgateway/routes/providers.py` (populate new fields)

- [ ] **Step 1: Add new fields to ProviderMetadataInfo**

Edit `src/webgateway/schemas.py`. Replace `ProviderMetadataInfo` class with:

```python
class ProviderMetadataInfo(BaseModel):
    name: str
    self_hosted: bool
    data_retention_days: int | None = None
    trains_on_queries: bool | None = None
    gdpr_compliant: bool = False
    hipaa_compliant: bool = False
    data_residency: list[str] = Field(default_factory=list)
    privacy_policy_url: str | None = None
    mcp_native: bool = False
    capabilities: list[str] = Field(default_factory=list)
    enabled: bool = True
    warnings: list[str] = Field(default_factory=list)
    stealth: bool = False
    engine: str | None = None
    firefox_version: str | None = None
    specialization: str | None = None
    cost_units_per_call: float = 1.0
```

- [ ] **Step 2: Update GET /providers route**

Edit `src/webgateway/routes/providers.py`. Update the response builder to include new fields:

```python
@router.get("/providers", response_model=list[ProviderMetadataInfo])
async def list_providers(
    request: Request,
    key: Annotated[AuthKey, Depends(verify_auth)],
) -> list[ProviderMetadataInfo]:
    """List all registered providers and their metadata."""
    registry: ProviderRegistry = request.app.state.provider_registry
    return [
        ProviderMetadataInfo(
            name=meta.name,
            self_hosted=meta.self_hosted,
            data_retention_days=meta.data_retention_days,
            trains_on_queries=meta.trains_on_queries,
            gdpr_compliant=meta.gdpr_compliant,
            hipaa_compliant=meta.hipaa_compliant,
            data_residency=list(meta.data_residency),
            privacy_policy_url=meta.privacy_policy_url,
            mcp_native=meta.mcp_native,
            capabilities=list(meta.capabilities),
            warnings=list(meta.warnings),
            stealth=meta.stealth,
            engine=meta.engine,
            firefox_version=meta.firefox_version,
            specialization=meta.specialization,
            cost_units_per_call=meta.cost_units_per_call,
        )
        for meta in registry.list_metadata()
    ]
```

- [ ] **Step 3: Verify providers endpoint still works**

Run: `source .venv/bin/activate && python -c "
from webgateway.schemas import ProviderMetadataInfo
p = ProviderMetadataInfo(name='test', self_hosted=False, warnings=['test warning'], stealth=True, engine='firefox')
print('OK:', p.model_dump())
"`
Expected: prints ProviderMetadataInfo with warnings/stealth/engine fields

- [ ] **Step 4: Commit**

```bash
git add src/webgateway/schemas.py src/webgateway/routes/providers.py
git commit -m "feat: add provider warnings and metadata fields to GET /providers"
```

---

### Task 11: Admin session routes

**Files:**
- Create: `src/webgateway/routes/sessions_admin.py`
- Modify: `src/webgateway/main.py` (include router)
- Create: `tests/unit/test_session_admin.py`

- [ ] **Step 1: Write failing test**

Write `tests/unit/test_session_admin.py`:

```python
from __future__ import annotations

import time
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from webgateway.main import create_app
from webgateway.sessions.manager import SessionManager
from webgateway.sessions.models import CookieEntry, SessionData
from webgateway.sessions.store import SessionStore


@pytest.fixture
def store_path(tmp_path: Path) -> str:
    p = tmp_path / "sessions"
    p.mkdir(parents=True)
    return str(p)


@pytest.fixture
def key() -> str:
    return Fernet.generate_key().decode()


@pytest.fixture
def store(store_path: str, key: str) -> SessionStore:
    return SessionStore(store_path, key)


@pytest.fixture
def app(store: SessionStore) -> TestClient:
    application = create_app()
    application.state.session_store = store
    application.state.session_manager = SessionManager(
        store,
        type("Config", (), {
            "auto_invalidate_on_login_wall": True,
            "strict_proxy_binding": True,
            "login_wall_patterns": ["Sign in"],
        })(),
    )

    # Override auth dependencies for testing
    from webgateway.auth import verify_admin
    from webgateway.config import AuthKey
    application.dependency_overrides[verify_admin] = lambda: AuthKey(
        id="test_admin", secret="test", admin=True
    )

    return TestClient(application)


def _make_session(session_id: str = "sess_test_001") -> SessionData:
    return SessionData(
        session_id=session_id,
        browser_service="invisible_playwright",
        domain="example.com",
        cookies=[CookieEntry(name="sid", value="abc", domain="example.com")],
        user_agent="Mozilla/5.0 Firefox/150.0",
        fingerprint_id="fp_01",
        created_ts=time.time(),
        last_used_ts=time.time(),
    )


class TestSessionAdmin:
    def test_create_session(self, app: TestClient):
        resp = app.post(
            "/admin/sessions/create",
            json={
                "session_id": "sess_test_001",
                "browser": "invisible_playwright",
                "domain": "example.com",
                "cookies": [{"name": "sid", "value": "abc", "domain": "example.com"}],
                "user_agent": "Mozilla/5.0 Firefox/150.0",
                "fingerprint_id": "fp_01",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "sess_test_001"

    def test_list_sessions(self, app: TestClient, store: SessionStore):
        store.save(_make_session("sess_list_001"))
        resp = app.get("/admin/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        # Cookie values should never appear
        body = resp.text
        assert "abc" not in body  # cookie value not in response

    def test_session_status(self, app: TestClient, store: SessionStore):
        store.save(_make_session("sess_status_001"))
        resp = app.get("/admin/sessions/sess_status_001/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "sess_status_001"
        assert data["valid"] is True

    def test_invalidate_by_id(self, app: TestClient, store: SessionStore):
        store.save(_make_session("sess_inv_001"))
        resp = app.post("/admin/sessions/invalidate", json={"session_id": "sess_inv_001"})
        assert resp.status_code == 200
        assert not store.exists("sess_inv_001")

    def test_refresh_cookies(self, app: TestClient, store: SessionStore):
        store.save(_make_session("sess_ref_001"))
        resp = app.post(
            "/admin/sessions/sess_ref_001/refresh",
            json={
                "cookies": [{"name": "new_sid", "value": "new_value", "domain": "example.com"}],
            },
        )
        assert resp.status_code == 200
        session = store.load("sess_ref_001")
        assert session.cookies[0].name == "new_sid"
        assert session.cookies[0].value == "new_value"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && pytest tests/unit/test_session_admin.py -v --tb=short 2>&1 | head -20`
Expected: ImportError for the admin router

- [ ] **Step 3: Implement admin session routes**

Write `src/webgateway/routes/sessions_admin.py`:

```python
from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from webgateway.auth import verify_admin
from webgateway.config import AuthKey
from webgateway.schemas import (
    CookieEntrySchema,
    SessionCreateRequest,
    SessionErrorResponse,
    SessionInfoResponse,
    SessionInvalidateRequest,
    SessionRefreshRequest,
    SessionStatusResponse,
)
from webgateway.sessions.manager import SessionError, SessionManager
from webgateway.sessions.models import CookieEntry, SessionData, session_to_info
from webgateway.sessions.store import SessionStore

router = APIRouter(tags=["admin"])


def _get_session_manager(request: Request) -> SessionManager:
    sm: SessionManager | None = getattr(request.app.state, "session_manager", None)
    if sm is None:
        raise HTTPException(status_code=503, detail="Session manager not available")
    return sm


def _get_session_store(request: Request) -> SessionStore:
    ss: SessionStore | None = getattr(request.app.state, "session_store", None)
    if ss is None:
        raise HTTPException(status_code=503, detail="Session store not available")
    return ss


@router.post("/admin/sessions/create", response_model=SessionInfoResponse)
async def create_session(
    body: SessionCreateRequest,
    request: Request,
    key: Annotated[AuthKey, Depends(verify_admin)],
) -> SessionInfoResponse:
    """Create a new encrypted session file."""
    store = _get_session_store(request)
    now = time.time()
    session = SessionData(
        session_id=body.session_id,
        browser_service=body.browser,
        domain=body.domain,
        cookies=[
            CookieEntry(
                name=c.name,
                value=c.value,
                domain=c.domain,
                path=c.path,
                expiry=c.expiry,
                secure=c.secure,
                http_only=c.http_only,
            )
            for c in body.cookies
        ],
        user_agent=body.user_agent,
        fingerprint_id=body.fingerprint_id,
        created_ts=now,
        last_used_ts=now,
        expiry_ts=body.expiry.timestamp() if body.expiry else None,
        proxy_binding=body.proxy_binding,
        strict_proxy=body.strict_proxy,
    )
    store.save(session)
    info = session_to_info(session)
    return SessionInfoResponse(**{
        k: getattr(info, k) for k in SessionInfoResponse.model_fields
    })


@router.get("/admin/sessions", response_model=list[SessionInfoResponse])
async def list_sessions(
    request: Request,
    key: Annotated[AuthKey, Depends(verify_admin)],
) -> list[SessionInfoResponse]:
    """List all sessions (metadata only — no cookie values)."""
    store = _get_session_store(request)
    return [
        SessionInfoResponse(**{
            k: getattr(info, k) for k in SessionInfoResponse.model_fields
        })
        for info in store.list_sessions()
    ]


@router.get("/admin/sessions/{session_id}/status", response_model=SessionStatusResponse)
async def session_status(
    session_id: str,
    request: Request,
    key: Annotated[AuthKey, Depends(verify_admin)],
) -> SessionStatusResponse:
    """Return session validity and metadata."""
    store = _get_session_store(request)
    try:
        session = store.load(session_id)
    except SessionError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load session: {exc}")

    now = time.time()
    expired = session.expiry_ts is not None and now > session.expiry_ts

    return SessionStatusResponse(
        session_id=session.session_id,
        valid=not expired,
        expired=bool(expired),
        domain_bound=session.domain,
        browser=session.browser_service,
        fingerprint_id=session.fingerprint_id,
        last_used_ts=session.last_used_ts,
        use_count=session.use_count,
        proxy_binding=session.proxy_binding,
    )


@router.post("/admin/sessions/invalidate")
async def invalidate_sessions(
    body: SessionInvalidateRequest,
    request: Request,
    key: Annotated[AuthKey, Depends(verify_admin)],
) -> dict[str, object]:
    """Invalidate sessions by session_id, domain, or browser."""
    manager = _get_session_manager(request)
    if not any([body.session_id, body.domain, body.browser]):
        raise HTTPException(
            status_code=422,
            detail="Provide at least one of: session_id, domain, browser",
        )
    count = await manager.invalidate(
        session_id=body.session_id,
        domain=body.domain,
        browser=body.browser,
    )
    return {"status": "ok", "invalidated": count}


@router.post("/admin/sessions/{session_id}/refresh")
async def refresh_session(
    session_id: str,
    body: SessionRefreshRequest,
    request: Request,
    key: Annotated[AuthKey, Depends(verify_admin)],
) -> dict[str, object]:
    """Replace cookies on an existing session. All other metadata preserved."""
    store = _get_session_store(request)
    try:
        session = store.load(session_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Session not found: {exc}")

    session.cookies = [
        CookieEntry(
            name=c.name,
            value=c.value,
            domain=c.domain,
            path=c.path,
            expiry=c.expiry,
            secure=c.secure,
            http_only=c.http_only,
        )
        for c in body.cookies
    ]
    store.save(session)
    return {"status": "ok", "session_id": session_id}
```

- [ ] **Step 4: Add router to main.py**

Append to `src/webgateway/main.py` imports:

```python
from webgateway.routes.sessions_admin import router as sessions_admin_router
```

Add after `app.include_router(admin_router)`:

```python
app.include_router(sessions_admin_router)
```

- [ ] **Step 5: Add session error handler**

Add to `main.py` before `app.include_router(sessions_admin_router)` or after the existing error handlers:

```python
    @app.exception_handler(SessionError)
    async def session_error_handler(
        request: Request, exc: SessionError
    ) -> JSONResponse:
        status_map = {
            "session_expired": 419,
            "session_not_found": 404,
        }
        http_status = status_map.get(exc.error_class, 400)
        return JSONResponse(
            status_code=http_status,
            content={
                "error": {
                    "error_class": exc.error_class,
                    "session_id": exc.session_id,
                    "message": str(exc),
                }
            },
        )
```

Need to add import for `SessionError` in main.py:

```python
from webgateway.sessions.manager import SessionError
```

- [ ] **Step 6: Run test to verify it passes**

Run: `source .venv/bin/activate && pytest tests/unit/test_session_admin.py -v --tb=short 2>&1`
Expected: all tests pass

- [ ] **Step 7: Commit**

```bash
git add src/webgateway/routes/sessions_admin.py src/webgateway/main.py tests/unit/test_session_admin.py
git commit -m "feat: add admin session CRUD endpoints"
```

---

### Task 12: GatewayService integration (session resolution, cache bypass, login wall)

**Files:**
- Modify: `src/webgateway/service.py`

- [ ] **Step 1: Add session_manager parameter to GatewayService**

Edit `src/webgateway/service.py` `__init__`:

```python
from webgateway.sessions.manager import SessionError, SessionManager
```

Add parameter to `__init__`:

```python
        session_manager: SessionManager | None = None,
```

Add assignment:

```python
        self._session_manager = session_manager
```

- [ ] **Step 2: Add session resolution and cache bypass to extract flow**

In `GatewayService.extract()`, after the `if dry_run` block (line ~354) and before `start = time.perf_counter()` (line ~359), add:

```python
        # --- session resolution ---
        session_data: SessionData | None = None
        if request.session_profile is not None and self._session_manager is not None:
            # Extract domain from URL
            from urllib.parse import urlparse
            domain = urlparse(request.url).hostname or ""

            try:
                session_data = await self._session_manager.resolve(
                    request.session_profile,
                    provider_name=decision.provider,
                    domain=domain,
                    proxy_name=decision.proxy,
                )
            except SessionError as exc:
                latency_ms = max(1, int((time.perf_counter() - start) * 1000))
                await self._audit_logger.log(
                    AuditEntry(
                        request_id=request_id,
                        api_key_id=api_key_id,
                        type="extract",
                        url=request.url,
                        provider_used=decision.provider,
                        latency_ms=latency_ms,
                        status="error",
                        policy_matched=decision.policy_matched,
                        proxy_used=decision.proxy,
                        session_profile=request.session_profile,
                        session_valid=False,
                        session_expired=(exc.error_class == "session_expired"),
                    )
                )
                raise

            # Force cache bypass when session_profile is present
            # (overrides any user-provided cache settings)
            cache_read = False
            cache_write = False
```

Add imports needed at the top of file: `SessionData` from sessions.models.

Now modify the cache handling block (after `request.cache is not None`):

The existing code is:
```python
        if request.cache is not None:
            cache_read = cache_enabled and request.cache.read
            cache_write = cache_enabled and request.cache.write
            ttl_override = request.cache.ttl_override
```

This should remain, but session force-override happens AFTER this. The session block above sets `cache_read = False` and `cache_write = False` when `session_profile` is present, which overrides whatever request.cache said.

Wait - the issue is ordering. Currently:
```python
        cache_read = cache_enabled
        cache_write = cache_enabled
        ...
        if request.cache is not None:
            cache_read = cache_enabled and request.cache.read
            cache_write = cache_enabled and request.cache.write
            ...
```

And then if `session_profile` is set, I override:
```python
        if request.session_profile is not None and self._session_manager is not None:
            ...
            cache_read = False
            cache_write = False
```

This works because the cache_read/cache_write assignments happen after the `request.cache` block.

- [ ] **Step 3: Pass session data to ExtractOptions**

After session resolution, modify the `ExtractOptions` construction. The existing code is:

```python
        options = ExtractOptions(
            format=request.format,
            proxy_url=proxy_url,
            wait_for_selector=request.wait_for_selector,
            timeout=self._config_manager.config.defaults.timeout,
        )
```

Replace with:

```python
        options = ExtractOptions(
            format=request.format,
            proxy_url=proxy_url,
            wait_for_selector=request.wait_for_selector,
            timeout=self._config_manager.config.defaults.timeout,
            session_id=request.session_profile,
            session_cookies=(
                {c.name: c.value for c in session_data.cookies}
                if session_data is not None and session_data.cookies
                else None
            ),
            fingerprint_id=session_data.fingerprint_id if session_data is not None else None,
            user_agent=session_data.user_agent if session_data is not None else None,
        )
```

- [ ] **Step 4: Add login wall detection after provider call**

After `_execute_with_fallback` returns (after the try/except block), add login wall check. This goes after the latency_ms calculation (line ~471) and before `if self._resource_manager is not None`:

Insert this after `quality_passed` is defined:

```python
        # --- login wall detection (session requests only) ---
        if (
            request.session_profile is not None
            and self._session_manager is not None
            and self._config_manager.config.sessions.auto_invalidate_on_login_wall
        ):
            login_wall_patterns = self._config_manager.config.sessions.login_wall_patterns
            result_content = result.content if hasattr(result, 'content') else ''
            if result_content and any(
                pattern.lower() in result_content.lower()
                for pattern in login_wall_patterns
            ):
                await self._session_manager.invalidate(
                    session_id=request.session_profile
                )
                latency_ms = int((time.perf_counter() - start) * 1000)
                await self._audit_logger.log(
                    AuditEntry(
                        request_id=request_id,
                        api_key_id=api_key_id,
                        type="extract",
                        url=request.url,
                        provider_used=provider_used,
                        latency_ms=latency_ms,
                        status="error",
                        policy_matched=decision.policy_matched,
                        proxy_used=decision.proxy,
                        session_profile=request.session_profile,
                        session_valid=False,
                        session_expired=True,
                    )
                )
                raise SessionError(
                    "session_expired",
                    "Login wall detected. Session invalidated. Refresh cookies.",
                    session_id=request.session_profile,
                )
```

- [ ] **Step 5: Add session fields to audit log entries**

In the success audit log entry (around line 503-531), add session fields:

```python
                session_profile=request.session_profile,
                session_valid=True,
                fingerprint_id=(
                    session_data.fingerprint_id if session_data is not None else None
                ),
                browser_service=(
                    session_data.browser_service if session_data is not None else None
                ),
                browser_engine="firefox" if session_data is not None else None,
                firefox_version=(
                    self._config_manager.config.providers
                    .get(provider_used, type("", (), {"firefox_version": None})())
                    .firefox_version
                    if session_data is not None else None
                ),
```

- [ ] **Step 6: Run existing tests**

Run: `source .venv/bin/activate && pytest tests/unit/ -v --tb=short 2>&1 | tail -30`
Expected: all existing tests pass (new session tests also pass)

- [ ] **Step 7: Commit**

```bash
git add src/webgateway/service.py
git commit -m "feat: integrate session resolution, cache bypass, and login wall detection into GatewayService"
```

---

### Task 13: Wire SessionManager into main.py

**Files:**
- Modify: `src/webgateway/main.py`

- [ ] **Step 1: Add session initialization to lifespan**

Edit `src/webgateway/main.py`:

Add imports after existing session import:

```python
from webgateway.sessions.manager import SessionManager, SessionError
from webgateway.sessions.store import SessionStore
```

In the `lifespan` function, after `app.state.resource_manager = resource_manager` (line ~78), add:

```python
    # --- Session store ---
    encryption_key = config_manager.config.sessions.encryption_key
    if encryption_key:
        session_store = SessionStore(
            store_path=config_manager.config.sessions.store_path,
            encryption_key=encryption_key,
        )
        session_manager = SessionManager(
            session_store,
            config_manager.config.sessions,
        )
    else:
        session_store = None
        session_manager = None
    app.state.session_store = session_store
    app.state.session_manager = session_manager
```

Update `GatewayService` constructor call to pass `session_manager`:

```python
    gateway_service = GatewayService(
        config_manager,
        policy_engine,
        provider_registry,
        proxy_resolver,
        audit_logger,
        cache_store=cache_store,
        dlp_middleware=dlp_middleware,
        resource_manager=resource_manager,
        session_manager=session_manager,
    )
```

- [ ] **Step 2: Verify app starts**

Run: `source .venv/bin/activate && python -c "from webgateway.main import app; print('App loaded:', app.title)"`
Expected: prints `App loaded: WebGateway`

- [ ] **Step 3: Commit**

```bash
git add src/webgateway/main.py
git commit -m "feat: wire SessionStore and SessionManager into application lifespan"
```

---

### Task 14: Config.yaml + Docker Compose updates

**Files:**
- Modify: `config.yaml`
- Modify: `docker-compose.yml` (if it exists)

- [ ] **Step 1: Add stealth and session config**

Edit `config.yaml`:

Add after the `sessions:` section (around line 263):

```yaml
# ---------------------------------------------------------------------------
# Stealth Browser — invisible_playwright settings
# ---------------------------------------------------------------------------
stealth:
  fingerprint_rotation:
    same_domain_window_seconds: 3600
    pool_size: 10
```

Update `sessions:` section:

```yaml
sessions:
  store_path: sessions
  encryption_key: ${SESSION_ENCRYPTION_KEY}
  auto_invalidate_on_login_wall: true
  strict_proxy_binding: true
  login_wall_patterns:
    - "Sign in"
    - "Log in to continue"
    - "Subscribe to read"
    - "Create an account"
    - "Your session has expired"
    - "Please log in"
    - "Access restricted"
```

Add provider entry in `providers:` section:

```yaml
  # Stealth browser — C++-patched Firefox 150, undetectable scraping
  invisible_playwright:
    base_url: http://invisible-playwright:3001
    stealth: true
    engine: firefox
    firefox_version: "150"
    cost_units_per_call: 0.8
    specialization: stealth_primary
```

- [ ] **Step 2: Check if docker-compose.yml exists and add service**

If `docker-compose.yml` exists, add the invisible-playwright service. If not, skip this step.

```bash
ls docker-compose.yml 2>/dev/null && echo "exists" || echo "not found"
```

If it exists, add after the `services:` section appropriate placement:

```yaml
  # --- Stealth browser (C++-patched Firefox 150) ---
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

And add `STEALTH_PLAYWRIGHT_URL` to the gateway service environment:

```yaml
    environment:
      STEALTH_PLAYWRIGHT_URL: http://invisible-playwright:3001
```

- [ ] **Step 3: Verify config loads**

Run: `source .venv/bin/activate && python -c "
from webgateway.config import load_config
cm = load_config('config.yaml')
cfg = cm.config
print('stealth:', cfg.stealth.model_dump())
print('sessions.login_wall_patterns:', cfg.sessions.login_wall_patterns)
print('providers.invisible_playwright:', cfg.providers.get('invisible_playwright'))
"`
Expected: prints all the new config values

- [ ] **Step 4: Commit**

```bash
git add config.yaml docker-compose.yml
git commit -m "feat: add stealth config, session config, and docker-compose profile"
```

---

### Task 15: Integration / end-to-end tests

**Files:**
- Create: `tests/unit/test_gateway_session_integration.py`

- [ ] **Step 1: Write integration tests for the full session flow**

Write `tests/unit/test_gateway_session_integration.py`:

```python
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.fernet import Fernet

from webgateway.config import ConfigManager, GatewayConfig, SessionsConfig
from webgateway.sessions.manager import SessionError, SessionManager
from webgateway.sessions.models import CookieEntry, SessionData
from webgateway.sessions.store import SessionStore


@pytest.fixture
def store_path(tmp_path: Path) -> str:
    return str(tmp_path / "sessions")


@pytest.fixture
def key() -> str:
    return Fernet.generate_key().decode()


@pytest.fixture
def store(store_path: str, key: str) -> SessionStore:
    return SessionStore(store_path, key)


@pytest.fixture
def session_config() -> SessionsConfig:
    return SessionsConfig(
        auto_invalidate_on_login_wall=True,
        strict_proxy_binding=True,
        login_wall_patterns=["Sign in", "Subscribe to read"],
    )


@pytest.fixture
def manager(store: SessionStore, session_config: SessionsConfig) -> SessionManager:
    return SessionManager(store, session_config)


def _make_session(session_id: str = "sess_integ_001", **overrides) -> SessionData:
    defaults = dict(
        session_id=session_id,
        browser_service="invisible_playwright",
        domain="wsj.com",
        cookies=[CookieEntry(name="sid", value="abc", domain="wsj.com")],
        user_agent="Mozilla/5.0 Firefox/150.0",
        fingerprint_id="fp_01",
        created_ts=time.time(),
        last_used_ts=time.time(),
        expiry_ts=time.time() + 86400,
    )
    defaults.update(overrides)
    return SessionData(**defaults)


class TestSessionIntegration:
    async def test_resolve_updates_use_count(self, manager: SessionManager, store: SessionStore):
        store.save(_make_session("sess_count"))
        await manager.resolve(
            "sess_count",
            provider_name="invisible_playwright",
            domain="wsj.com",
            proxy_name=None,
        )
        session = store.load("sess_count")
        assert session.use_count == 1

    async def test_resolve_subdomain_matches(self, manager: SessionManager, store: SessionStore):
        store.save(_make_session("sess_sub", domain="wsj.com"))
        resolved = await manager.resolve(
            "sess_sub",
            provider_name="invisible_playwright",
            domain="www.wsj.com",
            proxy_name=None,
        )
        assert resolved is not None

    async def test_invalidate_expired_session(self, manager: SessionManager, store: SessionStore):
        store.save(_make_session("sess_exp", expiry_ts=time.time() - 1))
        with pytest.raises(SessionError) as exc:
            await manager.resolve(
                "sess_exp",
                provider_name="invisible_playwright",
                domain="wsj.com",
                proxy_name=None,
            )
        assert exc.value.error_class == "session_expired"
        assert not store.exists("sess_exp")

    async def test_browser_mismatch(self, manager: SessionManager, store: SessionStore):
        store.save(_make_session("sess_bm", browser_service="camoufox"))
        with pytest.raises(SessionError) as exc:
            await manager.resolve(
                "sess_bm",
                provider_name="invisible_playwright",
                domain="wsj.com",
                proxy_name=None,
            )
        assert exc.value.error_class == "session_browser_mismatch"

    async def test_invalidate_by_browser(self, manager: SessionManager, store: SessionStore):
        store.save(_make_session("sess_a", browser_service="invisible_playwright"))
        store.save(_make_session("sess_b", browser_service="camoufox"))
        count = await manager.invalidate(browser="camoufox")
        assert count == 1
        assert store.exists("sess_a")
        assert not store.exists("sess_b")

    async def test_proxy_binding_enforced(self, manager: SessionManager, store: SessionStore):
        store.save(_make_session(
            "sess_proxy",
            proxy_binding="residential_us",
            strict_proxy=True,
        ))
        with pytest.raises(SessionError) as exc:
            await manager.resolve(
                "sess_proxy",
                provider_name="invisible_playwright",
                domain="wsj.com",
                proxy_name="bad_proxy",
            )
        assert exc.value.error_class == "session_proxy_mismatch"

    async def test_proxy_binding_not_required_when_not_strict(
        self, manager: SessionManager, store: SessionStore
    ):
        store.save(_make_session(
            "sess_nonstrict",
            proxy_binding="residential_us",
            strict_proxy=False,
        ))
        resolved = await manager.resolve(
            "sess_nonstrict",
            provider_name="invisible_playwright",
            domain="wsj.com",
            proxy_name=None,
        )
        assert resolved is not None

    async def test_missing_session_raises(self, manager: SessionManager):
        with pytest.raises(SessionError) as exc:
            await manager.resolve(
                "nonexistent",
                provider_name="invisible_playwright",
                domain="wsj.com",
                proxy_name=None,
            )
        assert exc.value.error_class == "session_not_found"
```

- [ ] **Step 2: Run integration tests**

Run: `source .venv/bin/activate && pytest tests/unit/test_gateway_session_integration.py tests/unit/test_session_store.py tests/unit/test_session_manager.py tests/unit/test_session_admin.py -v --tb=short 2>&1`
Expected: all session-related tests pass

- [ ] **Step 3: Run full test suite**

Run: `source .venv/bin/activate && pytest tests/unit/ -v --tb=short 2>&1`
Expected: all tests pass

- [ ] **Step 4: Run lint check**

Run: `source .venv/bin/activate && ruff check src/webgateway/ 2>&1`
Expected: no lint errors (or only pre-existing ones)

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_gateway_session_integration.py
git commit -m "test: add session integration and edge case tests"
```
