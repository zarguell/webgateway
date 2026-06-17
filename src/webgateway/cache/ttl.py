"""TTL rule resolution for cached responses.

Given an ordered list of :class:`~webgateway.config.CacheTTLRule` objects, pick
the TTL of the first rule whose match criteria all pass. Falls back to
``default_ttl`` when nothing matches.
"""

from __future__ import annotations

import fnmatch
import re
from urllib.parse import urlparse

from webgateway.config import CacheTTLRule


def resolve_ttl(
    rules: list[CacheTTLRule],
    default_ttl: int,
    *,
    provider: str | None = None,
    url: str | None = None,
    content_type: str = "",
) -> int:
    """Return the TTL (seconds) of the first matching rule, else ``default_ttl``.

    A rule matches only when *every* criterion it specifies passes. Unspecified
    (``None``) criteria are ignored, so an empty match block always matches.
    """
    for rule in rules:
        if _rule_matches(rule, provider=provider, url=url, content_type=content_type):
            return rule.ttl
    return default_ttl


def _rule_matches(
    rule: CacheTTLRule,
    *,
    provider: str | None,
    url: str | None,
    content_type: str,
) -> bool:
    match = rule.match

    if match.provider is not None and (
        provider is None or provider not in match.provider
    ):
        return False

    if match.domain_glob is not None:
        host = _extract_host(url)
        if not any(fnmatch.fnmatch(host, pattern.lower()) for pattern in match.domain_glob):
            return False

    if match.content_type is not None and match.content_type != content_type:
        return False

    if match.url_pattern is not None and (  # noqa: SIM103
        url is None or re.search(match.url_pattern, url) is None
    ):
        return False

    return True


def _extract_host(url: str | None) -> str:
    """Return the lowercased hostname for matching, or ``""`` if absent."""
    if not url:
        return ""
    return urlparse(url).hostname or ""
