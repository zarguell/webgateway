# MCP Downstream Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose `web_search` and `web_extract` as MCP tools (Streamable HTTP transport) so AI agents can call WebGateway via MCP instead of (or alongside) REST.

**Architecture:** Mount a `FastMCP` Streamable HTTP ASGI sub-app inside the existing FastAPI process at `/mcp`. Both REST and MCP share the same `GatewayService` singleton — the MCP tools are thin wrappers that build `SearchRequest`/`ExtractRequest` from tool arguments and call `gateway_service.search()`/`gateway_service.extract()`. Auth reuses the existing Bearer token scheme via a Starlette middleware that validates tokens against `ConfigManager.find_auth_key()`.

**Tech Stack:** `mcp>=1.27,<2` (official Python SDK, v1.x stable), Starlette middleware, Pydantic v2, existing `GatewayService`.

---

## Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Transport | Streamable HTTP (`stateless_http=True`) | Stateless, multi-client, Docker-friendly |
| Path | `POST /mcp` (mounted at `/mcp`, `streamable_http_path="/"`) | Single port alongside REST |
| Auth | Starlette `BaseHTTPMiddleware` on the MCP sub-app | Reuses existing `ConfigManager.find_auth_key()`; avoids SDK's OAuth ceremony |
| `api_key_id` propagation | `contextvars.ContextVar` set by middleware | Preserves audit trail without changing `GatewayService` interface |
| Tool returns | JSON-serialized response models (strings) | Universally compatible with all MCP clients |
| Error handling | Catch `ProviderError`/`DlpBlockedError`, return structured JSON error strings | Agent-friendly; agents can parse error fields |
| Session manager | Manually entered in parent lifespan via `async with mcp.session_manager.run()` | Starlette does not propagate lifespan to mounted sub-apps |
| `session_profile` param | Omitted from v1 (sessions not implemented — build item #16) | YAGNI; add when session store exists |

## SDK API Reference (verified from `mcp` v1.28.0 source)

```python
from mcp.server.fastmcp import FastMCP

# Constructor accepts: name, json_response, stateless_http, streamable_http_path, ...
mcp = FastMCP("WebGateway", json_response=True, stateless_http=True, streamable_http_path="/")

# Tool registration (decorator MUST be called with parens)
@mcp.tool()
async def my_tool(param: str) -> str:
    """Description from docstring."""
    return "result"

# Get mountable ASGI app (does NOT block)
asgi_app = mcp.streamable_http_app()  # returns Starlette

# Session manager (MUST be running before requests arrive)
async with mcp.session_manager.run():
    # serve
```

---

## File Structure

| File | Responsibility | Action |
|------|---------------|--------|
| `src/webgateway/config.py` | Add `MCPConfig` model + `GatewayConfig.mcp` field | Modify |
| `src/webgateway/mcp/__init__.py` | Package init, exports | Create |
| `src/webgateway/mcp/server.py` | Tool execution functions, auth middleware, server factory | Create |
| `src/webgateway/main.py` | Conditionally mount MCP app in lifespan | Modify |
| `config.yaml` | Add MCP config block | Modify |
| `config.test.yaml` | Add MCP config block (enabled) | Modify |
| `Dockerfile` | Install `[mcp]` extra | Modify |
| `pyproject.toml` | Move `mcp` from optional to core deps | Modify |
| `tests/unit/test_mcp_server.py` | Unit tests for tools, middleware, factory | Create |

---

## Task 1: MCPConfig Model

**Files:**
- Modify: `src/webgateway/config.py` (add `MCPConfig` class ~line 219, add field to `GatewayConfig` ~line 209)
- Test: `tests/unit/test_mcp_server.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_mcp_server.py`:

```python
"""Unit tests for the MCP server module: config, tools, auth, factory."""

from __future__ import annotations

from webgateway.config import GatewayConfig, MCPConfig


# ---------------------------------------------------------------------------
# MCPConfig
# ---------------------------------------------------------------------------


class TestMCPConfig:
    def test_defaults(self):
        config = MCPConfig()
        assert config.enabled is False
        assert config.mount_path == "/mcp"
        assert config.json_response is True
        assert config.stateless is True

    def test_custom_values(self):
        config = MCPConfig(enabled=True, mount_path="/custom-mcp")
        assert config.enabled is True
        assert config.mount_path == "/custom-mcp"

    def test_gateway_config_has_mcp_field(self):
        config = GatewayConfig()
        assert hasattr(config, "mcp")
        assert isinstance(config.mcp, MCPConfig)
        assert config.mcp.enabled is False

    def test_gateway_config_parses_mcp_section(self):
        raw = {
            "mcp": {
                "enabled": True,
                "mount_path": "/mcp",
            }
        }
        config = GatewayConfig.model_validate(raw)
        assert config.mcp.enabled is True
        assert config.mcp.mount_path == "/mcp"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/unit/test_mcp_server.py::TestMCPConfig -v`

Expected: FAIL with `ImportError: cannot import name 'MCPConfig'`

- [ ] **Step 3: Implement MCPConfig**

Add to `src/webgateway/config.py` — insert the `MCPConfig` class **before** the `GatewayConfig` class (around line 208, after `CacheConfig`):

```python
class MCPConfig(BaseModel):
    """Configuration for the downstream MCP server.

    When enabled, a Streamable HTTP MCP endpoint is mounted at
    ``mount_path`` exposing ``web_search`` and ``web_extract`` tools.
    """

    enabled: bool = False
    mount_path: str = "/mcp"
    json_response: bool = True
    stateless: bool = True
```

Add the field to `GatewayConfig` (around line 219, inside the class body):

```python
    mcp: MCPConfig = Field(default_factory=MCPConfig)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/unit/test_mcp_server.py::TestMCPConfig -v`

Expected: PASS (4 tests)

- [ ] **Step 5: Run lint**

Run: `source .venv/bin/activate && ruff check src/webgateway/config.py`

Expected: No errors.

- [ ] **Step 6: Commit**

```bash
git add src/webgateway/config.py tests/unit/test_mcp_server.py
git commit -m "feat: add MCPConfig model for downstream MCP server settings"
```

---

## Task 2: Tool Execution Functions

These are standalone async functions that encapsulate the tool logic. They are independently testable with a mock `GatewayService` and later wrapped by thin closures in the MCP server factory.

**Files:**
- Create: `src/webgateway/mcp/__init__.py`
- Create: `src/webgateway/mcp/server.py`
- Test: `tests/unit/test_mcp_server.py` (append)

- [ ] **Step 1: Create the package init**

Create `src/webgateway/mcp/__init__.py`:

```python
"""Downstream MCP server package — exposes web_search and web_extract tools."""
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/unit/test_mcp_server.py`:

```python
import json
from unittest.mock import AsyncMock

import pytest

from webgateway.config import AuthKey
from webgateway.dlp import DlpBlockedError
from webgateway.mcp.server import execute_web_search, execute_web_extract
from webgateway.providers.base import ProviderError
from webgateway.schemas import SearchResponse, ExtractResponse


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _mock_service():
    """Create a mock GatewayService with async search/extract."""
    service = AsyncMock()
    return service


def _search_response():
    return SearchResponse(
        results=[],
        provider_used="searxng",
        request_id="req_test123",
        latency_ms=42,
    )


def _extract_response():
    return ExtractResponse(
        content="# Hello",
        format="markdown",
        url="https://example.com",
        provider_used="jina",
        request_id="req_test456",
        latency_ms=88,
    )


# ---------------------------------------------------------------------------
# execute_web_search
# ---------------------------------------------------------------------------


class TestExecuteWebSearch:
    @pytest.mark.asyncio
    async def test_success(self):
        service = _mock_service()
        service.search.return_value = _search_response()

        result = await execute_web_search(
            service, api_key_id="key1", query="python async"
        )

        service.search.assert_called_once()
        parsed = json.loads(result)
        assert parsed["provider_used"] == "searxng"
        assert parsed["request_id"] == "req_test123"

    @pytest.mark.asyncio
    async def test_passes_provider_hint(self):
        service = _mock_service()
        service.search.return_value = _search_response()

        await execute_web_search(
            service, api_key_id="key1", query="test", provider_hint="brave"
        )

        call_args = service.search.call_args
        request_arg = call_args[0][0]  # first positional arg
        assert request_arg.provider == "brave"

    @pytest.mark.asyncio
    async def test_provider_error_returns_error_json(self):
        service = _mock_service()
        service.search.side_effect = ProviderError(
            "searxng", "connection refused", status_code=503
        )

        result = await execute_web_search(
            service, api_key_id="key1", query="test"
        )

        parsed = json.loads(result)
        assert parsed["error"] == "provider_error"
        assert parsed["provider"] == "searxng"

    @pytest.mark.asyncio
    async def test_dlp_blocked_returns_error_json(self):
        service = _mock_service()
        service.search.side_effect = DlpBlockedError(
            policy="strict", matches=[]
        )

        result = await execute_web_search(
            service, api_key_id="key1", query="test"
        )

        parsed = json.loads(result)
        assert parsed["error"] == "dlp_blocked"


# ---------------------------------------------------------------------------
# execute_web_extract
# ---------------------------------------------------------------------------


class TestExecuteWebExtract:
    @pytest.mark.asyncio
    async def test_success(self):
        service = _mock_service()
        service.extract.return_value = _extract_response()

        result = await execute_web_extract(
            service, api_key_id="key1", url="https://example.com"
        )

        parsed = json.loads(result)
        assert parsed["content"] == "# Hello"
        assert parsed["provider_used"] == "jina"

    @pytest.mark.asyncio
    async def test_passes_format_and_provider(self):
        service = _mock_service()
        service.extract.return_value = _extract_response()

        await execute_web_extract(
            service,
            api_key_id="key1",
            url="https://example.com",
            format="html",
            provider_hint="firecrawl",
        )

        call_args = service.extract.call_args
        request_arg = call_args[0][0]
        assert request_arg.format == "html"
        assert request_arg.provider == "firecrawl"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `source .venv/bin/activate && python -m pytest tests/unit/test_mcp_server.py::TestExecuteWebSearch tests/unit/test_mcp_server.py::TestExecuteWebExtract -v`

Expected: FAIL with `ImportError: cannot import name 'execute_web_search'`

- [ ] **Step 4: Implement the tool execution functions**

Create `src/webgateway/mcp/server.py`:

```python
"""Downstream MCP server — exposes web_search and web_extract as MCP tools.

This module is built up across several tasks:

- **Tool execution functions** (Task 2): ``execute_web_search``, ``execute_web_extract``
- **Auth middleware** (Task 3): ``McpAuthMiddleware``
- **Server factory** (Task 4): ``create_mcp_server``

Transport: Streamable HTTP (stateless). Mounted as an ASGI sub-app inside the
existing FastAPI process at ``/mcp``. Shares the ``GatewayService`` singleton.
"""

from __future__ import annotations

import contextvars
import json

from webgateway.dlp import DlpBlockedError
from webgateway.providers.base import ProviderError
from webgateway.schemas import (
    ExtractRequest,
    SearchRequest,
)
from webgateway.service import GatewayService

__all__ = [
    "execute_web_extract",
    "execute_web_search",
    "mcp_api_key_id",
]

# Context variable set by McpAuthMiddleware (Task 3), read by tool wrappers
# (Task 4). Propagates within the same asyncio task (ASGI request flow).
mcp_api_key_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "mcp_api_key_id", default="mcp"
)


# ---------------------------------------------------------------------------
# Error helper
# ---------------------------------------------------------------------------


def _error_json(exc: Exception) -> str:
    """Serialize a known gateway exception into a JSON error string."""
    if isinstance(exc, DlpBlockedError):
        return json.dumps(
            {
                "error": "dlp_blocked",
                "message": str(exc),
                "policy": exc.policy,
                "matched_rules": exc.match_names,
            }
        )
    if isinstance(exc, ProviderError):
        return json.dumps(
            {
                "error": "provider_error",
                "provider": exc.provider,
                "message": str(exc),
                "upstream_status": exc.status_code,
            }
        )
    return json.dumps({"error": "internal_error", "message": str(exc)})


# ---------------------------------------------------------------------------
# Tool execution functions
# ---------------------------------------------------------------------------


async def execute_web_search(
    service: GatewayService,
    api_key_id: str,
    query: str,
    num_results: int = 10,
    provider_hint: str | None = None,
) -> str:
    """Execute a web search through the gateway pipeline.

    Returns JSON-serialized ``SearchResponse`` on success, or a JSON
    error object on failure.
    """
    try:
        result = await service.search(
            SearchRequest(
                query=query,
                num_results=num_results,
                provider=provider_hint,
            ),
            api_key_id=api_key_id,
        )
        return result.model_dump_json()
    except (DlpBlockedError, ProviderError) as exc:
        return _error_json(exc)


async def execute_web_extract(
    service: GatewayService,
    api_key_id: str,
    url: str,
    format: str = "markdown",
    provider_hint: str | None = None,
) -> str:
    """Execute a content extraction through the gateway pipeline.

    Returns JSON-serialized ``ExtractResponse`` on success, or a JSON
    error object on failure.
    """
    try:
        result = await service.extract(
            ExtractRequest(
                url=url,
                format=format,
                provider=provider_hint,
            ),
            api_key_id=api_key_id,
        )
        return result.model_dump_json()
    except (DlpBlockedError, ProviderError) as exc:
        return _error_json(exc)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `source .venv/bin/activate && python -m pytest tests/unit/test_mcp_server.py::TestExecuteWebSearch tests/unit/test_mcp_server.py::TestExecuteWebExtract -v`

Expected: PASS (6 tests)

- [ ] **Step 6: Run lint**

Run: `source .venv/bin/activate && ruff check src/webgateway/mcp/`

Expected: No errors.

- [ ] **Step 7: Commit**

```bash
git add src/webgateway/mcp/__init__.py src/webgateway/mcp/server.py tests/unit/test_mcp_server.py
git commit -m "feat: add MCP tool execution functions for web_search and web_extract"
```

---

## Task 3: Auth Middleware

Validates Bearer tokens on MCP requests using the same `ConfigManager.find_auth_key()` as the REST surface. Sets `mcp_api_key_id` contextvar for the audit trail.

**Files:**
- Modify: `src/webgateway/mcp/server.py` (add `McpAuthMiddleware` class)
- Test: `tests/unit/test_mcp_server.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_mcp_server.py`:

```python
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient

from webgateway.mcp.server import McpAuthMiddleware


def _make_test_app(config_manager):
    """Build a minimal Starlette app with McpAuthMiddleware for testing."""
    from starlette.routing import Route

    async def homepage(request):
        return PlainTextResponse("ok")

    inner = Starlette(routes=[Route("/", homepage)])
    inner.add_middleware(McpAuthMiddleware, config_manager=config_manager)
    return inner


def _make_config_manager_with_keys():
    """Build a real ConfigManager with test auth keys."""
    from webgateway.config import ConfigManager
    import tempfile, yaml, os
    
    config_data = {
        "auth": {
            "keys": [
                {"id": "agent-key", "secret": "test-token-123", "label": "test"},
                {"id": "admin-key", "secret": "admin-token-456", "admin": True},
            ]
        }
    }
    tmpdir = tempfile.mkdtemp()
    config_path = os.path.join(tmpdir, "config.yaml")
    with open(config_path, "w") as f:
        yaml.dump(config_data, f)
    
    return ConfigManager(config_path)


class TestMcpAuthMiddleware:
    def test_valid_token_passes_through(self):
        cm = _make_config_manager_with_keys()
        app = _make_test_app(cm)
        client = TestClient(app)
        
        r = client.get("/", headers={"Authorization": "Bearer test-token-123"})
        assert r.status_code == 200
        assert r.text == "ok"

    def test_missing_header_returns_401(self):
        cm = _make_config_manager_with_keys()
        app = _make_test_app(cm)
        client = TestClient(app)
        
        r = client.get("/")
        assert r.status_code == 401

    def test_invalid_token_returns_401(self):
        cm = _make_config_manager_with_keys()
        app = _make_test_app(cm)
        client = TestClient(app)
        
        r = client.get("/", headers={"Authorization": "Bearer wrong-token"})
        assert r.status_code == 401

    def test_malformed_header_returns_401(self):
        cm = _make_config_manager_with_keys()
        app = _make_test_app(cm)
        client = TestClient(app)
        
        r = client.get("/", headers={"Authorization": "Basic xyz"})
        assert r.status_code == 401

    def test_sets_api_key_id_contextvar(self):
        cm = _make_config_manager_with_keys()
        captured = {}

        from starlette.responses import JSONResponse
        from starlette.routing import Route

        async def capture_handler(request):
            from webgateway.mcp.server import mcp_api_key_id
            captured["key_id"] = mcp_api_key_id.get()
            return JSONResponse({"ok": True})

        inner = Starlette(routes=[Route("/", capture_handler)])
        inner.add_middleware(McpAuthMiddleware, config_manager=cm)
        client = TestClient(inner)
        
        client.get("/", headers={"Authorization": "Bearer admin-token-456"})
        assert captured["key_id"] == "admin-key"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && python -m pytest tests/unit/test_mcp_server.py::TestMcpAuthMiddleware -v`

Expected: FAIL with `ImportError: cannot import name 'McpAuthMiddleware'`

- [ ] **Step 3: Implement McpAuthMiddleware**

Add to `src/webgateway/mcp/server.py` — update the imports at the top and add the class. The full file after this step:

**Imports** — add after existing imports:

```python
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from webgateway.config import ConfigManager
```

**Add `McpAuthMiddleware` to `__all__`**:

```python
__all__ = [
    "McpAuthMiddleware",
    "execute_web_extract",
    "execute_web_search",
    "mcp_api_key_id",
]
```

**Add the class** (after the `_error_json` function, before the tool functions):

```python
# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------


class McpAuthMiddleware(BaseHTTPMiddleware):
    """Validates Bearer tokens on MCP endpoint requests.

    Uses the same ``ConfigManager.find_auth_key()`` as the REST auth
    dependencies. On success, sets the ``mcp_api_key_id`` contextvar
    so tool functions can include the key ID in the audit trail.
    """

    def __init__(self, app, config_manager: ConfigManager) -> None:
        super().__init__(app)
        self._config_manager = config_manager

    async def dispatch(self, request: Request, call_next):
        header = request.headers.get("Authorization", "")
        parts = header.split(" ", 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return JSONResponse(
                status_code=401,
                content={"error": "Missing or invalid Authorization header"},
            )
        token = parts[1].strip()
        key = self._config_manager.find_auth_key(token)
        if key is None:
            return JSONResponse(
                status_code=401,
                content={"error": "Invalid API key"},
            )
        mcp_api_key_id.set(key.id)
        return await call_next(request)
```

```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && python -m pytest tests/unit/test_mcp_server.py::TestMcpAuthMiddleware -v`

Expected: PASS (5 tests)

- [ ] **Step 5: Run lint**

Run: `source .venv/bin/activate && ruff check src/webgateway/mcp/server.py`

Expected: No errors.

- [ ] **Step 6: Commit**

```bash
git add src/webgateway/mcp/server.py tests/unit/test_mcp_server.py
git commit -m "feat: add McpAuthMiddleware for Bearer token validation on MCP surface"
```

---

## Task 4: MCP Server Factory

Builds a `FastMCP` instance, registers `web_search` and `web_extract` tools as thin closures over the execution functions, and configures transport settings.

**Files:**
- Modify: `src/webgateway/mcp/server.py` (add `create_mcp_server`)
- Test: `tests/unit/test_mcp_server.py` (append)

- [ ] **Step 0: Install the MCP dependency**

Run: `source .venv/bin/activate && pip install "mcp>=1.27,<2"`

This installs the official MCP Python SDK into the dev venv. The Dockerfile will be updated in Task 6 to install it in production.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_mcp_server.py`:

```python
class TestCreateMcpServer:
    def test_returns_fastmcp_instance(self):
        from mcp.server.fastmcp import FastMCP

        service = _mock_service()
        mcp_server = create_mcp_server(service)

        assert isinstance(mcp_server, FastMCP)

    def test_has_web_search_tool(self):
        service = _mock_service()
        mcp_server = create_mcp_server(service)

        # FastMCP stores tools in its _tool_manager
        tool_manager = mcp_server._tool_manager
        assert "web_search" in tool_manager._tools

    def test_has_web_extract_tool(self):
        service = _mock_service()
        mcp_server = create_mcp_server(service)

        tool_manager = mcp_server._tool_manager
        assert "web_extract" in tool_manager._tools

    def test_streamable_http_path_is_root(self):
        service = _mock_service()
        mcp_server = create_mcp_server(service)

        # When mounted at /mcp, the internal path must be "/" so
        # clients connect to http://host:port/mcp (not /mcp/mcp)
        assert mcp_server.settings.streamable_http_path == "/"

    def test_stateless_and_json_response(self):
        service = _mock_service()
        mcp_server = create_mcp_server(service)

        assert mcp_server.settings.stateless_http is True
        assert mcp_server.settings.json_response is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && python -m pytest tests/unit/test_mcp_server.py::TestCreateMcpServer -v`

Expected: FAIL with `ImportError: cannot import name 'create_mcp_server'`

- [ ] **Step 3: Implement create_mcp_server**

Add to `src/webgateway/mcp/server.py`. Add to `__all__`:

```python
__all__ = [
    "McpAuthMiddleware",
    "create_mcp_server",
    "execute_web_extract",
    "execute_web_search",
    "mcp_api_key_id",
]
```

Add the import and factory function at the bottom of the file:

```python
from mcp.server.fastmcp import FastMCP


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------


def create_mcp_server(gateway_service: GatewayService) -> FastMCP:
    """Build a FastMCP server with web_search and web_extract tools.

    The returned instance is ready for ``streamable_http_app()`` and
    ``session_manager.run()``. Transport settings are configured for
    stateless Streamable HTTP.

    Args:
        gateway_service: The shared :class:`GatewayService` singleton.
            All tool calls delegate to this instance.

    Returns:
        A configured :class:`FastMCP` instance.
    """
    mcp = FastMCP(
        "WebGateway",
        json_response=True,
        stateless_http=True,
        streamable_http_path="/",
    )

    @mcp.tool()
    async def web_search(
        query: str,
        num_results: int = 10,
        provider_hint: str | None = None,
    ) -> str:
        """Search the web. Provider selected automatically by policy engine.

        Args:
            query: The search query string.
            num_results: Maximum number of results to return (default: 10).
            provider_hint: Optional provider name to prefer
                (e.g. "searxng", "brave", "tavily").
        """
        return await execute_web_search(
            gateway_service,
            mcp_api_key_id.get(),
            query,
            num_results,
            provider_hint,
        )

    @mcp.tool()
    async def web_extract(
        url: str,
        format: str = "markdown",
        provider_hint: str | None = None,
    ) -> str:
        """Extract content from a URL. Provider selected automatically by policy.

        Args:
            url: The URL to extract content from.
            format: Output format: "markdown", "html", or "json"
                (default: "markdown").
            provider_hint: Optional provider name to prefer
                (e.g. "jina", "firecrawl").
        """
        return await execute_web_extract(
            gateway_service,
            mcp_api_key_id.get(),
            url,
            format,
            provider_hint,
        )

    return mcp
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && python -m pytest tests/unit/test_mcp_server.py::TestCreateMcpServer -v`

Expected: PASS (5 tests)

- [ ] **Step 5: Run full test suite for MCP module**

Run: `source .venv/bin/activate && python -m pytest tests/unit/test_mcp_server.py -v`

Expected: ALL PASS (19 tests total across all classes)

- [ ] **Step 6: Run lint**

Run: `source .venv/bin/activate && ruff check src/webgateway/mcp/`

Expected: No errors.

- [ ] **Step 7: Commit**

```bash
git add src/webgateway/mcp/server.py tests/unit/test_mcp_server.py
git commit -m "feat: add create_mcp_server factory with web_search and web_extract tools"
```

---

## Task 5: Wire MCP Into main.py

Conditionally mount the MCP ASGI sub-app when `config.mcp.enabled` is `True`. The session manager is started explicitly in the lifespan (Starlette does not propagate lifespan to mounted sub-apps).

**Files:**
- Modify: `src/webgateway/main.py`

- [ ] **Step 1: Write a smoke test**

Append to `tests/unit/test_mcp_server.py`:

```python
class TestMainAppMcpMount:
    def test_mcp_disabled_by_default(self):
        """When mcp.enabled is False, no MCP mount exists."""
        from webgateway.main import create_app

        app = create_app()
        # The lifespan hasn't run, but we can check routes
        # MCP is mounted dynamically in lifespan, so before startup
        # there should be no /mcp route
        mount_paths = [
            r.path for r in app.routes if hasattr(r, "path")
        ]
        assert "/mcp" not in mount_paths
```

- [ ] **Step 2: Run test to verify it passes (already true)**

Run: `source .venv/bin/activate && python -m pytest tests/unit/test_mcp_server.py::TestMainAppMcpMount -v`

Expected: PASS (MCP not yet wired, so no mount exists)

- [ ] **Step 3: Implement the lifespan changes**

Modify `src/webgateway/main.py`. Add imports at the top (after existing imports, before the lifespan function):

```python
from webgateway.mcp.server import McpAuthMiddleware, create_mcp_server
```

Modify the `lifespan` function. Replace the `yield` at the end with conditional MCP mounting. The new end of the lifespan function (after `app.state.gateway_service = gateway_service`):

```python
    # --- MCP server (optional) ---
    mcp_config = config_manager.config.mcp
    if mcp_config.enabled:
        mcp_server = create_mcp_server(gateway_service)
        mcp_app = mcp_server.streamable_http_app()
        mcp_app.add_middleware(
            McpAuthMiddleware, config_manager=config_manager
        )
        app.mount(mcp_config.mount_path, mcp_app)
        app.state.mcp_server = mcp_server

        # Session manager MUST be running before requests arrive.
        # Starlette does not propagate lifespan to mounted sub-apps,
        # so we enter it explicitly here.
        async with mcp_server.session_manager.run():
            yield
    else:
        yield
```

The full lifespan function after modification:

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialise all subsystems on startup.

    The ConfigManager is created first — every other component depends on it.
    All services are stored on ``app.state`` so route handlers can access them
    via ``request.app.state`` (explicit wiring, no global mutable state).
    """
    load_dotenv()

    config_path = os.environ.get("CONFIG_PATH", "config.yaml")
    config_manager = ConfigManager(config_path)
    app.state.config_manager = config_manager

    policy_engine = PolicyEngine(config_manager)
    app.state.policy_engine = policy_engine

    proxy_resolver = ProxyResolver(config_manager.config.proxies)
    app.state.proxy_resolver = proxy_resolver

    audit_logger = AuditLogger(config_manager.config.logging)
    app.state.audit_logger = audit_logger

    provider_registry = ProviderRegistry(config_manager)
    app.state.provider_registry = provider_registry

    cache_store = CacheStore(config_manager.config.cache.db_path)
    app.state.cache_store = cache_store

    dlp_middleware = DlpMiddleware(
        [p.model_dump() for p in config_manager.config.dlp_policies]
    )
    app.state.dlp_middleware = dlp_middleware

    gateway_service = GatewayService(
        config_manager,
        policy_engine,
        provider_registry,
        proxy_resolver,
        audit_logger,
        cache_store=cache_store,
        dlp_middleware=dlp_middleware,
    )
    app.state.gateway_service = gateway_service

    # --- MCP server (optional) ---
    mcp_config = config_manager.config.mcp
    if mcp_config.enabled:
        mcp_server = create_mcp_server(gateway_service)
        mcp_app = mcp_server.streamable_http_app()
        mcp_app.add_middleware(
            McpAuthMiddleware, config_manager=config_manager
        )
        app.mount(mcp_config.mount_path, mcp_app)
        app.state.mcp_server = mcp_server

        # Session manager MUST be running before requests arrive.
        # Starlette does not propagate lifespan to mounted sub-apps,
        # so we enter it explicitly here.
        async with mcp_server.session_manager.run():
            yield
    else:
        yield
```

- [ ] **Step 4: Verify lint**

Run: `source .venv/bin/activate && ruff check src/webgateway/main.py`

Expected: No errors.

- [ ] **Step 5: Verify all unit tests still pass**

Run: `source .venv/bin/activate && python -m pytest tests/unit/ -v`

Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/webgateway/main.py tests/unit/test_mcp_server.py
git commit -m "feat: wire MCP server into FastAPI lifespan with conditional mount"
```

---

## Task 6: Config Files, Dockerfile, pyproject.toml

**Files:**
- Modify: `config.yaml`
- Modify: `config.test.yaml`
- Modify: `Dockerfile`
- Modify: `pyproject.toml`

- [ ] **Step 1: Add MCP config to config.yaml**

Add at the end of `config.yaml` (after the `cache:` section):

```yaml
# ---------------------------------------------------------------------------
# MCP Server — downstream interface for AI agents
# ---------------------------------------------------------------------------
# When enabled, exposes web_search and web_extract as MCP tools via
# Streamable HTTP at POST /{mount_path}. Auth uses the same Bearer tokens
# as the REST API.
# ---------------------------------------------------------------------------
mcp:
  enabled: false               # set true to activate the MCP endpoint
  mount_path: /mcp
  json_response: true
  stateless: true
```

- [ ] **Step 2: Add MCP config to config.test.yaml**

Add at the end of `config.test.yaml`:

```yaml
# MCP server enabled for integration testing
mcp:
  enabled: true
  mount_path: /mcp
  json_response: true
  stateless: true
```

- [ ] **Step 3: Move mcp from optional to core deps in pyproject.toml**

In `pyproject.toml`, change line 13 area. Move `"mcp>=1.27,<2"` from `[project.optional-dependencies]` to `dependencies`:

Change `dependencies` to include mcp:

```toml
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.32.0",
    "httpx[socks]>=0.28.0",
    "pydantic>=2.10.0",
    "pydantic-settings>=2.6.0",
    "pyyaml>=6.0.2",
    "python-dotenv>=1.0.1",
    "cryptography>=44.0.0",
    "mcp>=1.27,<2",
]
```

Remove the `[project.optional-dependencies]` `mcp` section (lines 31-33):

```toml
# Remove this block:
# mcp = [
#     "mcp>=1.0.0",
# ]
```

Keep the `dev` section.

- [ ] **Step 4: Update Dockerfile to install mcp**

In `Dockerfile`, the builder stage already does `pip install --no-cache-dir .`. Since `mcp` is now a core dependency (Step 3), this will install it automatically. No Dockerfile change needed — **verify** this by checking that `mcp` appears in `dependencies` (not `optional-dependencies`).

Actually — the `COPY pyproject.toml .` on Dockerfile line 11 copies the full pyproject.toml, and `pip install .` reads `dependencies`. Since we moved `mcp` to core deps, it will be installed. **No Dockerfile change needed.**

- [ ] **Step 5: Install updated deps in dev venv**

Run: `source .venv/bin/activate && pip install -e ".[dev]"`

This ensures the dev environment has the updated dependency list.

- [ ] **Step 6: Run full unit test suite**

Run: `source .venv/bin/activate && make test-unit`

Expected: ALL PASS

- [ ] **Step 7: Run lint**

Run: `source .venv/bin/activate && make lint`

Expected: No errors.

- [ ] **Step 8: Commit**

```bash
git add config.yaml config.test.yaml pyproject.toml
git commit -m "feat: enable MCP config and move mcp to core dependencies"
```

---

## Verification Checklist (run after all tasks)

- [ ] `make test-unit` passes with all MCP tests green
- [ ] `make lint` passes clean
- [ ] `docker compose -f docker-compose.test.yml up -d --build` starts successfully
- [ ] `curl -s http://localhost:8080/mcp` returns 401 (auth required)
- [ ] `curl -s -H "Authorization: Bearer test-agent-key" -X POST http://localhost:8080/mcp -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","method":"tools/list","id":1}'` returns `web_search` and `web_extract` in the tool list
- [ ] `curl -s -H "Authorization: Bearer test-agent-key" -X POST http://localhost:8080/mcp -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"web_search","arguments":{"query":"python tutorial"}},"id":2}'` returns search results

## Out of Scope (Deferred)

- **Upstream MCP client (PRD #14)** — Only revisit if a provider has MCP-only capabilities with no REST API
- **`session_profile` parameter** — Add when session/cookie jar store (PRD #16) is implemented
- **`dry_run` support via MCP** — Dry-run is a REST debugging feature; MCP tools always execute
- **MCP SDK v2 migration** — Pin `mcp>=1.27,<2`; migrate after v2 stable release (target July 2026)
