"""GET /providers endpoint.

Returns static metadata for every registered provider, including data
residency, compliance flags, and capabilities. Used by operators and the DLP
engine to understand provider characteristics.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from webgateway.auth import verify_auth
from webgateway.config import AuthKey
from webgateway.providers.registry import ProviderRegistry
from webgateway.schemas import ProviderMetadataInfo

router = APIRouter(tags=["providers"])


@router.get("/providers", response_model=list[ProviderMetadataInfo])
async def list_providers(
    request: Request,
    key: Annotated[AuthKey, Depends(verify_auth)],
) -> list[ProviderMetadataInfo]:
    """List all registered providers and their metadata."""
    registry: ProviderRegistry = request.app.state.provider_registry
    return [
        ProviderMetadataInfo(
            name=meta.name,
            self_hosted=meta.self_hosted,
            data_retention_days=meta.data_retention_days,
            trains_on_queries=meta.trains_on_queries,
            gdpr_compliant=meta.gdpr_compliant,
            hipaa_compliant=meta.hipaa_compliant,
            data_residency=list(meta.data_residency),
            privacy_policy_url=meta.privacy_policy_url,
            mcp_native=meta.mcp_native,
            capabilities=list(meta.capabilities),
            warnings=list(meta.warnings),
            stealth=meta.stealth,
            engine=meta.engine,
            firefox_version=meta.firefox_version,
            specialization=meta.specialization,
            cost_units_per_call=meta.cost_units_per_call,
        )
        for meta in registry.list_metadata()
    ]
