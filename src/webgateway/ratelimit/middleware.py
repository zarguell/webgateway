"""FastAPI middleware that enforces per-key and per-IP rate limits."""

from __future__ import annotations

import logging

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from webgateway.ratelimit.limiter import RateLimitExceeded, SlidingWindowRateLimiter

logger = logging.getLogger(__name__)

_RATE_LIMITED_PATHS = ("/search", "/extract", "/mcp")


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window rate limiting for search/extract endpoints.

    Reads the ``SlidingWindowRateLimiter`` instance from ``app.state.rate_limiter``,
    which is initialized during the application lifespan.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        limiter: SlidingWindowRateLimiter | None = getattr(
            request.app.state, "rate_limiter", None
        )
        if limiter is None:
            return await call_next(request)

        config = request.app.state.config_manager.config.rate_limiting
        if not config.enabled:
            return await call_next(request)

        # Only rate-limit the data endpoints, not health/admin/docs
        if not any(request.url.path.startswith(p) for p in _RATE_LIMITED_PATHS):
            return await call_next(request)

        checks: list[tuple[str, int, int]] = []

        # Per-IP limit
        client_host = request.client.host if request.client else "unknown"
        checks.append((
            f"ip:{client_host}",
            config.by_ip.requests,
            config.by_ip.window_seconds,
        ))

        # Per-key limit (if authenticated)
        api_key_id: str | None = getattr(request.state, "api_key_id", None)
        if api_key_id:
            checks.append((
                f"key:{api_key_id}",
                config.by_key.requests,
                config.by_key.window_seconds,
            ))

        try:
            await limiter.check_multi(checks)
        except RateLimitExceeded as exc:
            logger.warning("Rate limit exceeded: %s", exc)
            return Response(
                status_code=429,
                content=(
                    f'{{"error":"rate_limit_exceeded",'
                    f'"detail":"{exc}",'
                    f'"retry_after":{exc.window_seconds}}}'
                ),
                media_type="application/json",
                headers={"Retry-After": str(exc.window_seconds)},
            )

        return await call_next(request)
