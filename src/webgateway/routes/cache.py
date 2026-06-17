from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from webgateway.auth import verify_admin
from webgateway.cache.store import CacheStore
from webgateway.config import AuthKey
from webgateway.schemas import (
    CacheFlushResponse,
    CacheInvalidateRequest,
    CacheInvalidateResponse,
    CacheStatsResponse,
)

router = APIRouter(prefix="/admin/cache", tags=["admin"])


def _get_store(request: Request) -> CacheStore:
    store: CacheStore | None = getattr(request.app.state, "cache_store", None)
    if store is None:
        raise RuntimeError("Cache store not initialised")
    return store


@router.post("/invalidate", response_model=CacheInvalidateResponse)
async def invalidate(
    request: Request,
    body: CacheInvalidateRequest,
    key: Annotated[AuthKey, Depends(verify_admin)],
) -> CacheInvalidateResponse:
    store = _get_store(request)
    count = await store.invalidate(
        url=body.url,
        url_pattern=body.url_pattern,
        provider=body.provider,
    )
    return CacheInvalidateResponse(invalidated=count)


@router.post("/flush", response_model=CacheFlushResponse)
async def flush(
    request: Request,
    key: Annotated[AuthKey, Depends(verify_admin)],
) -> CacheFlushResponse:
    store = _get_store(request)
    count = await store.flush()
    return CacheFlushResponse(flushed=count)


@router.get("/stats", response_model=CacheStatsResponse)
async def stats(
    request: Request,
    key: Annotated[AuthKey, Depends(verify_admin)],
) -> CacheStatsResponse:
    store = _get_store(request)
    data = await store.stats()
    return CacheStatsResponse(**data)
