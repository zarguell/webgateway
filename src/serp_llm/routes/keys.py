"""API key management REST endpoints.

Requires admin authentication. Keys live in the SQLite-backed ``KeyStore``.
Plaintext secrets are returned exactly once on creation and never stored.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from serp_llm.auth import verify_admin
from serp_llm.config import AuthKey
from serp_llm.key_store import KeyNotFound, KeyStore
from serp_llm.schemas import (
    CreateKeyRequest,
    CreateKeyResponse,
    KeyInfoResponse,
    RevokeKeyResponse,
)

router = APIRouter(prefix="/admin/keys", tags=["keys"])


def _get_key_store(request: Request) -> KeyStore:
    ks: KeyStore | None = getattr(request.app.state, "key_store", None)
    if ks is None:
        raise HTTPException(status_code=503, detail="Key store not available")
    return ks


@router.get("/list", response_model=list[KeyInfoResponse])
async def list_keys(
    request: Request,
    key: Annotated[AuthKey, Depends(verify_admin)],
) -> list[KeyInfoResponse]:
    """List all API keys (metadata only — no secret values)."""
    store = _get_key_store(request)
    stored_keys = store.list_keys()
    return [
        KeyInfoResponse(
            key_id=k.id,
            label=k.label,
            role=k.role,
            created_ts=k.created_ts,
            last_used_ts=k.last_used_ts,
            revoked=k.revoked,
            revoked_ts=k.revoked_ts,
            secret_prefix=k.id[:8] if k.id else "",
        )
        for k in stored_keys
    ]


@router.post("/create", response_model=CreateKeyResponse)
async def create_key(
    body: CreateKeyRequest,
    request: Request,
    key: Annotated[AuthKey, Depends(verify_admin)],
) -> CreateKeyResponse:
    """Create a new API key.

    The plaintext ``secret`` in the response is shown **exactly once**.
    Only the bcrypt hash is persisted — if you lose the secret you must
    revoke this key and create a new one.
    """
    if body.role not in ("operator", "admin"):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid role {body.role!r}; must be 'operator' or 'admin'",
        )
    store = _get_key_store(request)
    stored, plaintext = store.create_key(label=body.label, role=body.role)
    return CreateKeyResponse(
        key_id=stored.id,
        secret=plaintext,
        label=stored.label,
        role=stored.role,
    )


@router.post("/{key_id}/revoke", response_model=RevokeKeyResponse)
async def revoke_key(
    key_id: str,
    request: Request,
    key: Annotated[AuthKey, Depends(verify_admin)],
) -> RevokeKeyResponse:
    """Revoke an API key immediately. No restart required."""
    store = _get_key_store(request)
    try:
        stored = store.revoke_key(key_id)
    except KeyNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RevokeKeyResponse(
        key_id=stored.id,
        revoked=stored.revoked,
        revoked_ts=stored.revoked_ts,  # type: ignore[arg-type]
    )
