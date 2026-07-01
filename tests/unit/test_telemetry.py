"""Unit tests for telemetry IP resolution."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import Response

from serp_llm.middleware.telemetry import TelemetryMiddleware
from serp_llm.telemetry import TelemetryConfig, resolve_client_ip


class _FakeRequest:
    """Minimal request stub — just enough to test resolve_client_ip."""

    def __init__(self, client_host: str, xff: str | None = None):
        self.client = _FakeClient(client_host)
        self._xff = xff

    @property
    def headers(self):
        class _Headers:
            def __init__(self, xff):
                self._xff = xff

            def get(self, key, default=""):
                if key == "X-Forwarded-For" and self._xff is not None:
                    return self._xff
                return default

        return _Headers(self._xff)


class _FakeClient:
    def __init__(self, host: str):
        self.host = host


def test_no_proxy_no_xff_returns_client_host():
    """When proxy trust is disabled, return request.client.host."""
    req = _FakeRequest(client_host="203.0.113.5")
    assert resolve_client_ip(req, enabled=False) == "203.0.113.5"


def test_no_proxy_with_xff_returns_client_host():
    """When proxy trust is disabled, ignore X-Forwarded-For."""
    req = _FakeRequest(client_host="203.0.113.5", xff="1.2.3.4")
    assert resolve_client_ip(req, enabled=False) == "203.0.113.5"


def test_enabled_but_not_from_trusted_proxy_returns_client_host():
    """Connected directly (not from a trusted CIDR) — don't trust XFF."""
    req = _FakeRequest(
        client_host="203.0.113.5",
        xff="1.2.3.4",
    )
    result = resolve_client_ip(
        req,
        enabled=True,
        trusted_cidrs=["10.0.0.0/8", "172.16.0.0/12"],
    )
    assert result == "203.0.113.5"


def test_coming_from_trusted_proxy_uses_xff():
    """Connected from a trusted proxy CIDR — use XFF leftmost IP."""
    req = _FakeRequest(
        client_host="10.0.0.42",
        xff="198.51.100.7",
    )
    result = resolve_client_ip(
        req,
        enabled=True,
        trusted_cidrs=["10.0.0.0/8"],
    )
    assert result == "198.51.100.7"


def test_trusted_proxy_with_chain_returns_leftmost():
    """Multi-hop XFF: client, proxy1, proxy2 — return the client IP."""
    req = _FakeRequest(
        client_host="10.0.0.42",
        xff="198.51.100.7, 10.0.0.1, 172.16.0.1",
    )
    result = resolve_client_ip(
        req,
        enabled=True,
        trusted_cidrs=["10.0.0.0/8", "172.16.0.0/12"],
    )
    assert result == "198.51.100.7"


def test_unknown_client_host_returns_unknown():
    """When request.client is None, return 'unknown'."""
    req = _FakeRequest(client_host="unknown")
    req.client = None  # simulate missing client info
    assert resolve_client_ip(req, enabled=True) == "unknown"


def test_enabled_empty_xff_returns_client_host():
    """Trusted proxy but no XFF header — fall back to client.host."""
    req = _FakeRequest(client_host="10.0.0.42")
    result = resolve_client_ip(
        req,
        enabled=True,
        trusted_cidrs=["10.0.0.0/8"],
    )
    assert result == "10.0.0.42"


# ---------------------------------------------------------------------------
# TelemetryMiddleware tests
# ---------------------------------------------------------------------------


async def _dummy_handler(request):
    """Simple handler that returns a response and asserts state."""
    assert hasattr(request.state, "client_ip"), "client_ip not set by middleware"
    return Response(status_code=200, content=b"ok")


async def test_middleware_sets_client_ip_with_default_config():
    """With config absent, client_ip falls back to request.client.host."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/search",
        "headers": [],
        "client": ("203.0.113.5", 54321),
        "app": type("App", (), {"state": type("State", (), {"telemetry_config": None})})(),
    }
    req = Request(scope)
    middleware = TelemetryMiddleware(_dummy_handler)
    response = await middleware.dispatch(req, _dummy_handler)
    assert response.status_code == 200


async def test_middleware_disabled_uses_raw_ip():
    """When telemetry config has enabled=False, use request.client.host."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/search",
        "headers": [],
        "client": ("10.0.0.5", 54321),
        "app": type(
            "App",
            (),
            {
                "state": type(
                    "State",
                    (),
                    {"telemetry_config": TelemetryConfig(enabled=False)},
                )()
            },
        )(),
    }
    req = Request(scope)
    middleware = TelemetryMiddleware(_dummy_handler)
    await middleware.dispatch(req, _dummy_handler)
    assert req.state.client_ip == "10.0.0.5"


async def test_middleware_trusts_xff_when_enabled():
    """When enabled and request from trusted CIDR, use XFF leftmost IP."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/search",
        "headers": [
            (b"x-forwarded-for", b"198.51.100.7"),
        ],
        "client": ("10.0.0.42", 54321),
        "app": type(
            "App",
            (),
            {
                "state": type(
                    "State",
                    (),
                    {
                        "telemetry_config": TelemetryConfig(
                            enabled=True,
                            trusted_cidrs=["10.0.0.0/8"],
                        )
                    },
                )()
            },
        )(),
    }
    req = Request(scope)
    middleware = TelemetryMiddleware(_dummy_handler)
    await middleware.dispatch(req, _dummy_handler)
    assert req.state.client_ip == "198.51.100.7"
