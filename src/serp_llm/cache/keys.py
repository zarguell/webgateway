"""Deterministic cache key derivation.

Keys are SHA-256 hex digests of stable, prefixed input strings so that callers
never need to worry about hashing — they pass the natural parameters of the
request and receive an opaque cache key.
"""

from __future__ import annotations

import hashlib


def search_key(query: str, provider: str, num_results: int) -> str:
    """Return a cache key for a search request."""
    raw = f"search:{query}:{provider}:{num_results}"
    return hashlib.sha256(raw.encode()).hexdigest()


def extract_key(
    url: str,
    format: str,
    session_profile: str | None,
    provider: str | None = None,
    *,
    pp_skip: bool = False,
) -> str:
    """Return a cache key for an extraction request.

    ``session_profile`` is part of the key so that two profiles with different
    cookies or identities do not collide on the same URL.

    ``provider`` ensures that explicit provider requests (e.g. ``firecrawl``
    vs ``jina``) get separate cache entries even for the same URL.

    ``pp_skip`` ensures that requests with ``post_processing.skip=True``
    (raw unprocessed content) don't collide with processed content.
    """
    raw = f"extract:{url}:{format}:{session_profile or ''}:{provider or ''}:pp_skip={pp_skip}"
    return hashlib.sha256(raw.encode()).hexdigest()
