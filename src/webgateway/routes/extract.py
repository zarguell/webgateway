"""POST /extract endpoint.

Validates auth, delegates to :class:`~webgateway.service.GatewayService`, and
sets the ``X-Request-ID`` response header for cross-layer audit trail joins.

The endpoint is named ``/extract`` (not ``/scrape``) per the naming convention:
documentation may say "scrape", but tool calls and API endpoints use "extract".
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from webgateway.auth import verify_auth
from webgateway.config import AuthKey
from webgateway.schemas import DryRunResponse, ExtractRequest, ExtractResponse
from webgateway.service import GatewayService

router = APIRouter(tags=["extract"])


@router.post("/extract", response_model=ExtractResponse | DryRunResponse)
async def extract(
    request: Request,
    body: ExtractRequest,
    response: Response,
    key: Annotated[AuthKey, Depends(verify_auth)],
    dry_run: bool = Query(False, description="Preview policy decision without executing"),
) -> ExtractResponse | DryRunResponse:
    """Extract content from a URL. Provider selected automatically by policy."""
    service: GatewayService = request.app.state.gateway_service
    result = await service.extract(body, api_key_id=key.id, dry_run=dry_run)
    response.headers["X-Request-ID"] = result.request_id
    return result
