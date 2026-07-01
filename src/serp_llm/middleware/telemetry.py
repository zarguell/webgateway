"""Middleware that resolves the real client IP and stores it on request state.

Must be registered **before** ``RateLimitMiddleware`` so rate limiting can
use the resolved IP rather than the raw connection IP.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from serp_llm.telemetry import TelemetryConfig, resolve_client_ip


class TelemetryMiddleware(BaseHTTPMiddleware):
    """Parses ``X-Forwarded-For`` and sets ``request.state.client_ip``.

    Uses the ``TelemetryConfig`` from ``request.app.state`` or falls back
    to defaults (proxy trust disabled).
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Resolve source IP
        config: TelemetryConfig | None = getattr(
            request.app.state, "telemetry_config", None
        )
        if config is not None:
            client_ip = resolve_client_ip(
                request,
                enabled=config.enabled,
                trusted_cidrs=config.trusted_cidrs,
            )
        else:
            client_ip = resolve_client_ip(request, enabled=False)

        request.state.client_ip = client_ip

        response = await call_next(request)
        return response
