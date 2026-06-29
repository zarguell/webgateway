"""POST /search endpoint.

Validates auth, delegates to :class:`~serp_llm.service.GatewayService`, and
sets the ``X-Request-ID`` response header for cross-layer audit trail joins.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from serp_llm.auth import verify_auth
from serp_llm.config import AuthKey
from serp_llm.schemas import DryRunResponse, SearchRequest, SearchResponse
from serp_llm.service import GatewayService

router = APIRouter(tags=["search"])


@router.post(
    "/search",
    response_model=SearchResponse | DryRunResponse,
    summary="Execute a web search",
    description="Execute a web search through the gateway pipeline. The provider is selected "
    "automatically by the policy engine based on the query, domain, and configured rules. "
    "Supports cache, DLP scanning, circuit breaker, and quota enforcement. "
    "Use `?dry_run=true` to preview the policy decision without executing the search.\n\n"
    "**Policy behavior:** The policy engine evaluates rules in order. First match wins. "
    "If no rule matches, the default search provider is used from config.",
)
async def search(
    request: Request,
    body: SearchRequest,
    response: Response,
    key: Annotated[AuthKey, Depends(verify_auth)],
    dry_run: bool = Query(False, description="Preview policy decision without executing"),
) -> SearchResponse | DryRunResponse:
    """Search the web. Provider selected automatically by policy engine."""
    service: GatewayService = request.app.state.gateway_service
    result = await service.search(body, api_key_id=key.id, dry_run=dry_run)
    response.headers["X-Request-ID"] = result.request_id
    return result
