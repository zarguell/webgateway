"""Unit tests for the MCP server module: config, tools, auth, factory."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient

from serp_llm.config import GatewayConfig, MCPConfig
from serp_llm.dlp import DlpBlockedError
from serp_llm.mcp.server import (
    McpAuthMiddleware,
    create_mcp_server,
    execute_web_extract,
    execute_web_search,
    mcp_api_key_id,
    mount_mcp,
)
from serp_llm.providers.base import ProviderError
from serp_llm.schemas import ExtractResponse, SearchResponse


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
        raw = {"mcp": {"enabled": True, "mount_path": "/mcp"}}
        config = GatewayConfig.model_validate(raw)
        assert config.mcp.enabled is True
        assert config.mcp.mount_path == "/mcp"


def _mock_service():
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


class TestExecuteWebSearch:
    @pytest.mark.asyncio
    async def test_success(self):
        service = _mock_service()
        service.search.return_value = _search_response()
        result = await execute_web_search(service, api_key_id="key1", query="python async")
        service.search.assert_called_once()
        parsed = json.loads(result)
        assert parsed["provider_used"] == "searxng"
        assert parsed["request_id"] == "req_test123"

    @pytest.mark.asyncio
    async def test_passes_provider_hint(self):
        service = _mock_service()
        service.search.return_value = _search_response()
        await execute_web_search(service, api_key_id="key1", query="test", provider_hint="brave")
        request_arg = service.search.call_args[0][0]
        assert request_arg.provider == "brave"

    @pytest.mark.asyncio
    async def test_provider_error_returns_error_json(self):
        service = _mock_service()
        service.search.side_effect = ProviderError("searxng", "connection refused", status_code=503)
        result = await execute_web_search(service, api_key_id="key1", query="test")
        parsed = json.loads(result)
        assert parsed["error"] == "provider_error"
        assert parsed["provider"] == "searxng"

    @pytest.mark.asyncio
    async def test_dlp_blocked_returns_error_json(self):
        service = _mock_service()
        service.search.side_effect = DlpBlockedError(policy="strict", matches=[])
        result = await execute_web_search(service, api_key_id="key1", query="test")
        parsed = json.loads(result)
        assert parsed["error"] == "dlp_blocked"


class TestExecuteWebExtract:
    @pytest.mark.asyncio
    async def test_success(self):
        service = _mock_service()
        service.extract.return_value = _extract_response()
        result = await execute_web_extract(service, api_key_id="key1", url="https://example.com")
        parsed = json.loads(result)
        assert parsed["content"] == "# Hello"
        assert parsed["provider_used"] == "jina"

    @pytest.mark.asyncio
    async def test_passes_format_and_provider(self):
        service = _mock_service()
        service.extract.return_value = _extract_response()
        await execute_web_extract(
            service, api_key_id="key1", url="https://example.com",
            format="html", provider_hint="firecrawl",
        )
        request_arg = service.extract.call_args[0][0]
        assert request_arg.format == "html"
        assert request_arg.provider == "firecrawl"


def _make_test_app(config_manager):
    from starlette.routing import Route
    async def homepage(request):
        return PlainTextResponse("ok")
    inner = Starlette(routes=[Route("/", homepage)])
    inner.add_middleware(McpAuthMiddleware, config_manager=config_manager)
    return inner


def _make_config_manager_with_keys():
    import os
    import tempfile

    import yaml

    from serp_llm.config import ConfigManager
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
            captured["key_id"] = mcp_api_key_id.get()
            return JSONResponse({"ok": True})
        inner = Starlette(routes=[Route("/", capture_handler)])
        inner.add_middleware(McpAuthMiddleware, config_manager=cm)
        client = TestClient(inner)
        client.get("/", headers={"Authorization": "Bearer admin-token-456"})
        assert captured["key_id"] == "admin-key"


class TestCreateMcpServer:
    def test_returns_fastmcp_instance(self):
        from mcp.server.fastmcp import FastMCP
        service = _mock_service()
        mcp_server = create_mcp_server(service)
        assert isinstance(mcp_server, FastMCP)

    def test_has_web_search_tool(self):
        service = _mock_service()
        mcp_server = create_mcp_server(service)
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
        assert mcp_server.settings.streamable_http_path == "/"

    def test_stateless_and_json_response(self):
        service = _mock_service()
        mcp_server = create_mcp_server(service)
        assert mcp_server.settings.stateless_http is True
        assert mcp_server.settings.json_response is True


class TestMountMcp:
    """Tests for ``mount_mcp()``, which conditionally mounts the MCP sub-app."""

    def _make_config_manager(self, mcp_enabled: bool = False, mount_path: str = "/mcp"):
        """Build a ConfigManager with a given MCP config."""
        import os
        import tempfile

        import yaml

        from serp_llm.config import ConfigManager

        config_data = {
            "mcp": {
                "enabled": mcp_enabled,
                "mount_path": mount_path,
            },
            "auth": {"keys": []},
        }
        tmpdir = tempfile.mkdtemp()
        config_path = os.path.join(tmpdir, "config.yaml")
        with open(config_path, "w") as f:
            yaml.dump(config_data, f)
        return ConfigManager(config_path)

    def test_returns_none_when_disabled(self):
        config_manager = self._make_config_manager(mcp_enabled=False)
        service = _mock_service()
        app = FastAPI()

        ctx = mount_mcp(app, service, config_manager)

        assert ctx is None
        mount_paths = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/mcp" not in mount_paths

    def test_returns_context_manager_when_enabled(self):
        config_manager = self._make_config_manager(mcp_enabled=True)
        service = _mock_service()
        app = FastAPI()

        ctx = mount_mcp(app, service, config_manager)

        assert ctx is not None
        mount_paths = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/mcp" in mount_paths
        assert ctx.__aenter__ is not None  # it's an async context manager

    def test_mounts_at_custom_path(self):
        config_manager = self._make_config_manager(mcp_enabled=True, mount_path="/api/mcp")
        service = _mock_service()
        app = FastAPI()

        mount_mcp(app, service, config_manager)

        mount_paths = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/api/mcp" in mount_paths
