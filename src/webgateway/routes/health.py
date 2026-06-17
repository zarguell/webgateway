"""GET /health endpoint.

Public health check — no authentication required. Returns the service status
and the health of all registered providers.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request

from webgateway.resource_manager import ProviderResourceManager
from webgateway.schemas import HealthResponse, ProviderHealthInfo
from webgateway.service import GatewayService

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    """Return service status and per-provider health."""
    service: GatewayService = request.app.state.gateway_service
    health_map = await service.check_providers_health()
    now = datetime.now(UTC).isoformat()

    rm: ProviderResourceManager | None = getattr(
        request.app.state, "resource_manager", None
    )
    summary = await rm.get_summary() if rm else {}

    providers = [
        ProviderHealthInfo(
            name=name,
            healthy=healthy,
            last_check_ts=now,
            circuit_state=summary.get(name, {}).get("circuit_state") if rm else None,
            quota_pct=summary.get(name, {}).get("quota_pct") if rm else None,
        )
        for name, healthy in health_map.items()
    ]
    return HealthResponse(status="ok", providers=providers)
