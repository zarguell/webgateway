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

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from serp_llm.config import AuthKey, ConfigManager
from serp_llm.dlp import DlpBlockedError
from serp_llm.key_store import KeyStore
from serp_llm.providers.base import ProviderError
from serp_llm.schemas import (
    ExtractRequest,
    SearchRequest,
)
from serp_llm.service import GatewayService

__all__ = [
    "McpAuthMiddleware",
    "create_mcp_server",
    "execute_web_extract",
    "execute_web_search",
    "mcp_api_key_id",
    "mount_mcp",
]

# Context variable set by McpAuthMiddleware (Task 3), read by tool wrappers
# (Task 4). Propagates within the same asyncio task (ASGI request flow).
mcp_api_key_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "mcp_api_key_id", default="mcp"
)


def _error_json(exc: Exception) -> str:
    """Serialize a known gateway exception into a JSON error string."""
    if isinstance(exc, DlpBlockedError):
        return json.dumps({
            "error": "dlp_blocked",
            "message": str(exc),
            "policy": exc.policy,
            "matched_rules": exc.match_names,
        })
    if isinstance(exc, ProviderError):
        return json.dumps({
            "error": "provider_error",
            "provider": exc.provider,
            "message": str(exc),
            "upstream_status": exc.status_code,
        })
    return json.dumps({"error": "internal_error", "message": str(exc)})


# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------


class McpAuthMiddleware(BaseHTTPMiddleware):
    """Validates Bearer tokens on MCP endpoint requests.

    Uses multi-source key resolution matching REST auth:
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

    async def dispatch(self, request, call_next):
        header = request.headers.get("Authorization", "")
        parts = header.split(" ", 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return JSONResponse(
                status_code=401,
                content={"error": "Invalid or missing API key"},
            )
        token = parts[1].strip()

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
                return AuthKey(
                    id=stored.id,
                    secret=token,
                    label=stored.label,
                    admin=stored.is_admin,
                )

        # 3. Bootstrap key
        return self._check_bootstrap_key(token)

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
        return AuthKey(
            id="bootstrap",
            secret=bootstrap_secret,
            label="Bootstrap admin key",
            admin=True,
        )


async def execute_web_search(
    service: GatewayService,
    api_key_id: str,
    query: str,
    num_results: int = 10,
    provider_hint: str | None = None,
) -> str:
    """Execute a web search through the gateway pipeline.

    Returns JSON-serialized ``SearchResponse`` on success, or a JSON error
    on failure.
    """
    try:
        result = await service.search(
            SearchRequest(
                query=query, num_results=num_results, provider=provider_hint
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

    Returns JSON-serialized ``ExtractResponse`` on success, or a JSON error
    on failure.
    """
    try:
        result = await service.extract(
            ExtractRequest(
                url=url, format=format, provider=provider_hint
            ),
            api_key_id=api_key_id,
        )
        return result.model_dump_json()
    except (DlpBlockedError, ProviderError) as exc:
        return _error_json(exc)


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------

from mcp.server.fastmcp import FastMCP  # noqa: E402


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
        "serpLLM",
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


# ---------------------------------------------------------------------------
# Lifespan mount helper
# ---------------------------------------------------------------------------


def mount_mcp(
    app: FastAPI,
    gateway_service: GatewayService,
    config_manager: ConfigManager,
):
    """Mount the MCP ASGI sub-app if enabled in config.

    Some MCP clients (OpenCode, issue #8058) send a GET probe to the MCP
    endpoint expecting SSE before attempting Streamable HTTP POST.  This
    GET handler returns a lightweight 200 so the client accepts the server
    as alive and proceeds with POST.  Using 200 instead of 405 avoids a
    known OpenCode bug where non-200 probe responses don't trigger fallback
    on Linux (issue #24946).

    Returns the session manager async context manager if MCP was mounted,
    or ``None`` when ``config.mcp.enabled`` is ``False``.

    Usage in a FastAPI lifespan::

        ctx = mount_mcp(app, gateway_service, config_manager)
        if ctx:
            async with ctx:
                yield
        else:
            yield
    """
    mcp_config = config_manager.config.mcp
    if not mcp_config.enabled:
        return None

    # FastAPI mounts are matched by path prefix, so a route registered on the
    # parent app takes precedence over the sub-app mount below.  Returns 200
    # with text/event-stream to satisfy OpenCode's SSE probe (issues #8058,
    # #24946) without adding actual SSE session state.
    @app.get(mcp_config.mount_path)
    @app.get(mcp_config.mount_path + "/")
    async def _mcp_probe():
        from starlette.responses import Response

        return Response(
            content="",
            status_code=200,
            media_type="text/plain",
        )

    mcp_server = create_mcp_server(gateway_service)
    mcp_app = mcp_server.streamable_http_app()
    key_store: KeyStore | None = getattr(app.state, "key_store", None)
    mcp_app.add_middleware(
        McpAuthMiddleware,
        config_manager=config_manager,
        key_store=key_store,
    )
    app.mount(mcp_config.mount_path, mcp_app)
    app.state.mcp_server = mcp_server
    return mcp_server.session_manager.run()
