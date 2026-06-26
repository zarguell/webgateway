"""SSRF protection via URL validation and private-IP blocklisting."""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_PRIVATE_NETWORKS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("fc00::/7"),
]

_BLOCKED_HOSTNAMES: set[str] = {
    "localhost",
    "localhost.localdomain",
    "127.0.0.1",
    "::1",
    "0.0.0.0",
}

_METADATA_HOST_SUFFIXES: set[str] = {
    ".internal",
    ".compute.internal",
    ".ec2.internal",
}

_ALLOWED_SCHEMES = {"http", "https"}


class UrlValidationError(ValueError):
    """Raised when a URL fails SSRF validation."""

    def __init__(self, url: str, reason: str) -> None:
        self.url = url
        self.reason = reason
        super().__init__(f"URL validation failed for {url!r}: {reason}")


def validate_url(url: str) -> None:
    """Validate a URL for SSRF safety.

    Raises ``UrlValidationError`` if the URL is invalid, points to a private
    or reserved IP, uses a disallowed scheme, or appears to target an internal
    metadata endpoint.
    """
    parsed = urlparse(url)

    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise UrlValidationError(
            url,
            f"Disallowed scheme {parsed.scheme!r} (only http/https allowed)",
        )

    hostname = parsed.hostname or ""

    if hostname.lower() in _BLOCKED_HOSTNAMES:
        raise UrlValidationError(url, f"Hostname {hostname} is blocked")

    for suffix in _METADATA_HOST_SUFFIXES:
        if hostname.lower().endswith(suffix):
            raise UrlValidationError(
                url, f"Hostname {hostname} appears to be an internal metadata endpoint"
            )

    _validate_ip(hostname, url)


def _validate_ip(hostname: str, url: str) -> None:
    try:
        addrinfo = socket.getaddrinfo(hostname, 80)
    except (socket.gaierror, OSError) as exc:
        raise UrlValidationError(
            url, f"Could not resolve hostname {hostname}: {exc}"
        ) from exc

    for _family, _, _, _, sockaddr in addrinfo:
        ip_str = sockaddr[0]
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            continue

        for network in _PRIVATE_NETWORKS:
            if addr in network:
                raise UrlValidationError(
                    url,
                    f"Resolved IP {ip_str} is in private/reserved range {network}",
                )


def is_safe_url(url: str) -> bool:
    """Return True if the URL passes SSRF validation, False otherwise."""
    try:
        validate_url(url)
        return True
    except UrlValidationError:
        return False
