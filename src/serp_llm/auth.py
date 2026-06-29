"""Bearer token authentication dependencies for FastAPI.

Supports three authentication sources in priority order:

1. **Config-based keys** — the legacy ``auth.keys`` in ``config.yaml``
2. **SQLite-backed keys** — the ``api_keys`` table managed by ``KeyStore``
3. **Bootstrap key** — from ``BOOTSTRAP_ADMIN_KEY`` env var (only when the
   ``api_keys`` table is empty)

On success, the matched ``AuthKey`` is returned and ``api_key_id`` is stored
on ``request.state`` for the audit logger.
"""

from __future__ import annotations

import logging
import os

from fastapi import HTTPException, Request

from serp_llm.config import AuthKey, ConfigManager
from serp_llm.key_store import KeyStore

__all__ = ["verify_auth", "verify_admin"]

logger = logging.getLogger(__name__)

_BOOTSTRAP_ENV_VAR = "BOOTSTRAP_ADMIN_KEY"


def _get_config_manager(request: Request) -> ConfigManager:
    """Retrieve the ConfigManager attached to the app at startup."""
    try:
        manager = request.app.state.config_manager
    except AttributeError:
        raise RuntimeError(
            "ConfigManager not found on app.state. "
            "Set app.state.config_manager = ConfigManager(...) at startup."
        ) from None
    return manager


def _get_key_store(request: Request) -> KeyStore | None:
    """Retrieve the KeyStore if attached, or None."""
    return getattr(request.app.state, "key_store", None)


def _extract_bearer_token(request: Request) -> str | None:
    """Extract the raw token from an ``Authorization: Bearer <token>`` header."""
    header = request.headers.get("Authorization", "")
    if not header:
        return None
    parts = header.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def _check_bootstrap_key(token: str, key_store: KeyStore | None) -> AuthKey | None:
    """Check if *token* matches the bootstrap admin key.

    The bootstrap key is valid only when:
    - The ``BOOTSTRAP_ADMIN_KEY`` env var is set
    - The ``api_keys`` table has zero admin keys (table is effectively empty)
    """
    bootstrap_secret = os.environ.get(_BOOTSTRAP_ENV_VAR)
    if not bootstrap_secret:
        return None
    if token != bootstrap_secret:
        return None
    # Only valid if no real admin keys exist yet
    if key_store is not None and key_store.count_active_admin_keys() > 0:
        return None
    return AuthKey(
        id="bootstrap",
        secret=bootstrap_secret,
        label="Bootstrap admin key",
        admin=True,
    )


def _find_key(token: str, request: Request) -> AuthKey | None:
    """Try all auth sources in priority order. Returns AuthKey or None."""

    # 1. Config-based keys (legacy)
    config_manager = _get_config_manager(request)
    key = config_manager.find_auth_key(token)
    if key is not None:
        return key

    # 2. SQLite-backed keys
    key_store = _get_key_store(request)
    if key_store is not None:
        stored = key_store.verify_key(token)
        if stored is not None:
            return AuthKey(
                id=stored.id,
                secret=token,
                label=stored.label,
                admin=(stored.role == "admin"),
            )

    # 3. Bootstrap key
    key = _check_bootstrap_key(token, key_store)
    if key is not None:
        return key

    return None


async def verify_auth(request: Request) -> AuthKey:
    """Validate the Bearer token against all auth sources.

    Checks config-based keys, SQLite-backed keys, and bootstrap key
    (in that order).

    Raises:
        HTTPException(401): If the token is missing, malformed, or unknown.
    """
    token = _extract_bearer_token(request)
    if token is None:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    key = _find_key(token, request)
    if key is None:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    # Expose the key *id* for audit logging downstream.
    request.state.api_key_id = key.id
    return key


async def verify_admin(request: Request) -> AuthKey:
    """Validate the token and require admin privileges.

    Raises:
        HTTPException(401): If the token is missing, malformed, or unknown.
        HTTPException(403): If the key is valid but not an admin key.
    """
    key = await verify_auth(request)
    if not key.admin:
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return key
