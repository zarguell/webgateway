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
from starlette.requests import Request
from starlette.responses import JSONResponse

from webgateway.config import ConfigManager
from webgateway.dlp import DlpBlockedError
from webgateway.providers.base import ProviderError
from webgateway.schemas import (
    ExtractRequest,
    SearchRequest,
)
from webgateway.service import GatewayService

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
                content={"error": "Invalid or missing API key"},
            )
        token = parts[1].strip()
        key = self._config_manager.find_auth_key(token)
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


# ---------------------------------------------------------------------------
# Lifespan mount helper
# ---------------------------------------------------------------------------


def mount_mcp(
    app: FastAPI,
    gateway_service: GatewayService,
    config_manager: ConfigManager,
):
    """Mount the MCP ASGI sub-app if enabled in config.

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

    mcp_server = create_mcp_server(gateway_service)
    mcp_app = mcp_server.streamable_http_app()
    mcp_app.add_middleware(McpAuthMiddleware, config_manager=config_manager)
    app.mount(mcp_config.mount_path, mcp_app)
    app.state.mcp_server = mcp_server
    return mcp_server.session_manager.run()
