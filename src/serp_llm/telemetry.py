"""Telemetry configuration and IP resolution utilities.

Provides the config model for trusted proxy settings and a pure function
that extracts the real client IP from ``X-Forwarded-For``, only trusting
the header when the direct connection came from a known proxy CIDR.
"""

from __future__ import annotations

import ipaddress
from typing import Any

from pydantic import BaseModel, Field


class TelemetryConfig(BaseModel):
    """Controls source-IP resolution from ``X-Forwarded-For``.

    When *enabled* is ``False`` (the default), the raw ``request.client.host``
    is always used — no proxy trust.
    """

    enabled: bool = False
    trusted_cidrs: list[str] = Field(
        default_factory=lambda: [
            "10.0.0.0/8",
            "172.16.0.0/12",
            "192.168.0.0/16",
        ],
        description=(
            "CIDR ranges treated as trusted proxies.  When a request's direct "
            "connection IP falls within one of these ranges, the ``X-Forwarded-For`` "
            "header is trusted and its *leftmost* IP is used as the client source."
        ),
    )


def resolve_client_ip(
    request: Any,
    *,
    enabled: bool = False,
    trusted_cidrs: list[str] | None = None,
) -> str:
    """Return the real client IP, respecting trusted proxy configuration.

    Parameters
    ----------
    request:
        A Starlette / FastAPI ``Request``-like object with ``.client.host``
        and ``.headers.get("X-Forwarded-For")``.
    enabled:
        Whether proxy trust is active.
    trusted_cidrs:
        CIDR ranges whose IPs are considered trusted proxies (only used
        when *enabled* is ``True``).

    Returns
    -------
    str
        The resolved client IP string, or ``"unknown"`` if no IP is
        available.
    """
    # Guard: no client info at all
    client = getattr(request, "client", None)
    if client is None:
        return "unknown"

    client_host: str = getattr(client, "host", "") or ""
    if not client_host:
        return "unknown"

    # Proxy trust disabled — return the direct connection IP
    if not enabled or not trusted_cidrs:
        return client_host

    # Is the direct connection from a known proxy?
    try:
        client_addr = ipaddress.ip_address(client_host)
    except ValueError:
        return client_host  # unparseable — don't trust XFF

    trusted_nets = [ipaddress.ip_network(cidr) for cidr in trusted_cidrs]
    if not any(client_addr in net for net in trusted_nets):
        return client_host  # not from a trusted proxy

    # Request came from a trusted proxy — parse X-Forwarded-For
    forwarded: str = request.headers.get("X-Forwarded-For", "")
    if not forwarded:
        return client_host

    # Leftmost IP is the original client
    ips = [ip.strip() for ip in forwarded.split(",")]
    return ips[0] if ips else client_host
