"""Per-request proxy resolver.

Proxies are *named* in ``config.yaml`` and referenced by policy rules. The
gateway never sets a proxy globally — the resolved URL string is injected
per-request into ``httpx.AsyncClient(proxy=...)`` (or a browser service call)
by the provider adapters.

PRD §9.1 / §4.4: supported proxy types are HTTP CONNECT and SOCKS5. Adapters
receive only the resolved URL; they never know the proxy's name or type.
"""

from __future__ import annotations

from serp_llm.config import ProxyConfig

__all__ = ["ProxyResolver"]


class ProxyResolver:
    """Look up named proxies and return their URL strings.

    Initialised with the ``proxies`` dict straight from ``GatewayConfig``
    (``config.proxies``). Callers pass the proxy *name* they got from a policy
    rule; this class resolves it to a usable URL or ``None``.
    """

    def __init__(self, proxies: dict[str, ProxyConfig]) -> None:
        self._proxies = proxies

    def resolve(self, proxy_name: str | None) -> str | None:
        """Return the proxy URL for *proxy_name*.

        Returns ``None`` when *proxy_name* is ``None`` (no proxy requested) or
        when the name is not present in the config. This is the lenient lookup
        used for optional proxy injection.
        """
        if proxy_name is None:
            return None
        proxy = self._proxies.get(proxy_name)
        if proxy is None:
            return None
        return proxy.url

    def get_httpx_proxy_url(self, proxy_name: str | None) -> str | None:
        """Return the proxy URL for *proxy_name*, raising if the name is unknown.

        Returns ``None`` when *proxy_name* is ``None`` (no proxy requested).

        Raises:
            ValueError: If *proxy_name* is non-``None`` but not found in the
                config — i.e. a policy rule referenced a proxy that doesn't
                exist, which is a configuration error.
        """
        if proxy_name is None:
            return None
        proxy = self._proxies.get(proxy_name)
        if proxy is None:
            raise ValueError(
                f"Proxy {proxy_name!r} not found in configuration. "
                f"Known proxies: {sorted(self._proxies)}"
            )
        return proxy.url
