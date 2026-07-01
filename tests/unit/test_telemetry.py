"""Unit tests for telemetry IP resolution."""

from __future__ import annotations

from serp_llm.telemetry import resolve_client_ip


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
