"""POST /admin/reload endpoint.

Hot-reloads the YAML configuration at runtime. Requires admin privileges.
Returns the new config hash so callers can verify the reload took effect.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from serp_llm.auth import verify_admin
from serp_llm.config import AuthKey, ConfigManager
from serp_llm.resource_manager import ProviderResourceManager
from serp_llm.schemas import (
    CircuitResetRequest,
    QuotaOverrideRequest,
    QuotaResetRequest,
    ReloadResponse,
    UsageHistoryItem,
    UsageSummaryItem,
    UsageSummaryResponse,
)

router = APIRouter(tags=["admin"])


@router.post("/admin/reload", response_model=ReloadResponse)
async def reload_config(
    request: Request,
    key: Annotated[AuthKey, Depends(verify_admin)],
) -> ReloadResponse:
    """Hot-reload the configuration file."""
    config_manager: ConfigManager = request.app.state.config_manager
    config_manager.reload()
    return ReloadResponse(reloaded=True, config_hash=config_manager.config_hash)


@router.get("/admin/usage/summary", response_model=UsageSummaryResponse)
async def usage_summary(
    request: Request,
    key: Annotated[AuthKey, Depends(verify_admin)],
) -> UsageSummaryResponse:
    """Return per-provider usage summary and circuit state."""
    rm: ProviderResourceManager | None = getattr(request.app.state, "resource_manager", None)
    if rm is None:
        return UsageSummaryResponse(providers=[])
    summary = await rm.get_summary()
    items = [
        UsageSummaryItem(
            provider=name,
            circuit_state=info["circuit_state"],
            calls_today=info["calls_today"],
            calls_month=info["calls_month"],
            limit_month=info["limit_month"],
            quota_pct=info["quota_pct"],
        )
        for name, info in summary.items()
    ]
    return UsageSummaryResponse(providers=items)


@router.get("/admin/usage/history", response_model=list[UsageHistoryItem])
async def usage_history(
    request: Request,
    key: Annotated[AuthKey, Depends(verify_admin)],
    provider: str,
    days: int = 30,
) -> list[UsageHistoryItem]:
    """Return daily usage history for a provider."""
    rm: ProviderResourceManager | None = getattr(request.app.state, "resource_manager", None)
    if rm is None:
        return []
    return await rm.get_history(provider, days)


@router.post("/admin/quota/reset")
async def quota_reset(
    body: QuotaResetRequest,
    request: Request,
    key: Annotated[AuthKey, Depends(verify_admin)],
) -> dict[str, str]:
    """Reset monthly usage for a provider."""
    rm: ProviderResourceManager | None = getattr(request.app.state, "resource_manager", None)
    if rm is not None:
        await rm.reset_quota(body.provider)
    return {"status": "ok", "provider": body.provider}


@router.post("/admin/quota/override")
async def quota_override(
    body: QuotaOverrideRequest,
    request: Request,
    key: Annotated[AuthKey, Depends(verify_admin)],
) -> dict[str, str]:
    """Override remaining quota for a provider."""
    rm: ProviderResourceManager | None = getattr(request.app.state, "resource_manager", None)
    if rm is not None:
        await rm.override_quota(body.provider, body.remaining)
    return {"status": "ok", "provider": body.provider}


@router.post("/admin/circuit/reset")
async def circuit_reset(
    body: CircuitResetRequest,
    request: Request,
    key: Annotated[AuthKey, Depends(verify_admin)],
) -> dict[str, str]:
    """Force-close a circuit breaker."""
    rm: ProviderResourceManager | None = getattr(request.app.state, "resource_manager", None)
    if rm is not None:
        await rm.reset_circuit(body.provider)
    return {"status": "ok", "provider": body.provider}
