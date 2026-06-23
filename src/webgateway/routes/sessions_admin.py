from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from webgateway.auth import verify_admin
from webgateway.config import AuthKey
from webgateway.schemas import (
    SessionCreateRequest,
    SessionInfoResponse,
    SessionInvalidateRequest,
    SessionRefreshRequest,
    SessionStatusResponse,
)
from webgateway.sessions.manager import SessionManager
from webgateway.sessions.models import CookieEntry, SessionData, session_to_info
from webgateway.sessions.store import SessionNotFound, SessionStore

router = APIRouter(tags=["admin"])


def _get_session_manager(request: Request) -> SessionManager:
    sm: SessionManager | None = getattr(request.app.state, "session_manager", None)
    if sm is None:
        raise HTTPException(status_code=503, detail="Session manager not available")
    return sm


def _get_session_store(request: Request) -> SessionStore:
    ss: SessionStore | None = getattr(request.app.state, "session_store", None)
    if ss is None:
        raise HTTPException(status_code=503, detail="Session store not available")
    return ss


@router.post("/admin/sessions/create", response_model=SessionInfoResponse)
async def create_session(
    body: SessionCreateRequest,
    request: Request,
    key: Annotated[AuthKey, Depends(verify_admin)],
) -> SessionInfoResponse:
    """Create a new encrypted session file."""
    store = _get_session_store(request)
    now = time.time()
    session = SessionData(
        session_id=body.session_id,
        browser_service=body.browser,
        domain=body.domain,
        cookies=[
            CookieEntry(
                name=c.name,
                value=c.value,
                domain=c.domain,
                path=c.path,
                expiry=c.expiry,
                secure=c.secure,
                http_only=c.http_only,
            )
            for c in body.cookies
        ],
        user_agent=body.user_agent,
        fingerprint_id=body.fingerprint_id,
        created_ts=now,
        last_used_ts=now,
        expiry_ts=body.expiry.timestamp() if body.expiry else None,
        proxy_binding=body.proxy_binding,
        strict_proxy=body.strict_proxy,
    )
    store.save(session)
    info = session_to_info(session)
    return SessionInfoResponse(**{
        k: getattr(info, k) for k in SessionInfoResponse.model_fields
    })


@router.get("/admin/sessions/list", response_model=list[SessionInfoResponse])
async def list_sessions(
    request: Request,
    key: Annotated[AuthKey, Depends(verify_admin)],
) -> list[SessionInfoResponse]:
    """List all sessions (metadata only — no cookie values)."""
    store = _get_session_store(request)
    return [
        SessionInfoResponse(**{
            k: getattr(info, k) for k in SessionInfoResponse.model_fields
        })
        for info in store.list_sessions()
    ]


@router.get("/admin/sessions/{session_id}/status", response_model=SessionStatusResponse)
async def session_status(
    session_id: str,
    request: Request,
    key: Annotated[AuthKey, Depends(verify_admin)],
) -> SessionStatusResponse:
    """Return session validity and metadata."""
    store = _get_session_store(request)
    try:
        session = store.load(session_id)
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail=f"Session {session_id!r} not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Failed to decrypt session: {exc}") from exc

    now = time.time()
    expired = session.expiry_ts is not None and now > session.expiry_ts

    return SessionStatusResponse(
        session_id=session.session_id,
        valid=not expired,
        expired=bool(expired),
        domain_bound=session.domain,
        browser=session.browser_service,
        fingerprint_id=session.fingerprint_id,
        last_used_ts=session.last_used_ts,
        use_count=session.use_count,
        proxy_binding=session.proxy_binding,
    )


@router.post("/admin/sessions/invalidate")
async def invalidate_sessions(
    body: SessionInvalidateRequest,
    request: Request,
    key: Annotated[AuthKey, Depends(verify_admin)],
) -> dict[str, object]:
    """Invalidate sessions by session_id, domain, or browser."""
    manager = _get_session_manager(request)
    if not any([body.session_id, body.domain, body.browser]):
        raise HTTPException(
            status_code=422,
            detail="Provide at least one of: session_id, domain, browser",
        )
    count = await manager.invalidate(
        session_id=body.session_id,
        domain=body.domain,
        browser=body.browser,
    )
    return {"status": "ok", "invalidated": count}


@router.post("/admin/sessions/{session_id}/refresh")
async def refresh_session(
    session_id: str,
    body: SessionRefreshRequest,
    request: Request,
    key: Annotated[AuthKey, Depends(verify_admin)],
) -> dict[str, object]:
    """Replace cookies on an existing session. All other metadata preserved."""
    store = _get_session_store(request)
    try:
        session = store.load(session_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Session not found: {exc}") from exc

    session.cookies = [
        CookieEntry(
            name=c.name,
            value=c.value,
            domain=c.domain,
            path=c.path,
            expiry=c.expiry,
            secure=c.secure,
            http_only=c.http_only,
        )
        for c in body.cookies
    ]
    store.save(session)
    return {"status": "ok", "session_id": session_id}
