"""Exemption enforcement for prompt injection detection (PRD §27.5).

Trusted domains and API keys can be exempted from detection entirely.
Domain matching supports exact match and wildcard patterns (*.example.com).
"""

from __future__ import annotations

import fnmatch
from urllib.parse import urlparse


def is_exempt(
    url: str,
    api_key_id: str,
    exempt_domains: list[str],
    exempt_api_key_ids: list[str],
) -> bool:
    """Check if a request is exempt from injection detection.

    Returns True if either:
    - The request's API key ID is in ``exempt_api_key_ids``
    - The request URL's domain matches any pattern in ``exempt_domains``
      (exact match or fnmatch glob for wildcards)
    """
    # API key exemption — highest priority
    if api_key_id in exempt_api_key_ids:
        return True

    # Domain exemption
    if not exempt_domains:
        return False

    parsed = urlparse(url)
    domain = parsed.hostname or ""

    for pattern in exempt_domains:
        if "*" in pattern or "?" in pattern or "[" in pattern:
            if fnmatch.fnmatch(domain, pattern):
                return True
        else:
            if domain == pattern:
                return True

    return False
