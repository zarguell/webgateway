"""Rate limiting middleware — uses ``BaseHTTPMiddleware`` with an explicit
``fastapi_app`` reference passed at construction time to avoid ``request.app``
resolution issues.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from webgateway.ratelimit.limiter import RateLimitExceeded, SlidingWindowRateLimiter

logger = logging.getLogger(__name__)

_RATE_LIMITED_PATHS = ("/search", "/extract", "/mcp")


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window rate limiting for search/extract endpoints.

    Accepts the ``FastAPI`` app instance via the ``fastapi_app`` keyword
    argument so it can read ``app.state.rate_limiter`` and config at request
    time without relying on ``request.app`` resolution.
    """

    def __init__(self, app, *, fastapi_app: FastAPI) -> None:
        super().__init__(app)
        self._fastapi_app = fastapi_app

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        limiter: SlidingWindowRateLimiter | None = getattr(
            self._fastapi_app.state, "rate_limiter", None
        )
        if limiter is None:
            return await call_next(request)

        config = self._fastapi_app.state.config_manager.config.rate_limiting
        if not config.enabled:
            return await call_next(request)

        if not any(request.url.path.startswith(p) for p in _RATE_LIMITED_PATHS):
            return await call_next(request)

        checks: list[tuple[str, int, int]] = []

        client_host = request.client.host if request.client else "unknown"
        checks.append((
            f"ip:{client_host}",
            config.by_ip.requests,
            config.by_ip.window_seconds,
        ))

        # Parse the Bearer token directly so per-key limiting works even though
        # the FastAPI auth dependency runs inside the route handler (after this
        # middleware). This duplicates auth parsing but avoids the ordering
        # dependency.
        api_key_id = self._extract_api_key_id(request)
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

    @staticmethod
    def _extract_api_key_id(request: Request) -> str | None:
        """Extract the API key ID from the Authorization header.

        Checks the Bearer token against the app's configured keys without
        requiring the FastAPI auth dependency to have run first.
        """
        header = request.headers.get("Authorization", "")
        parts = header.split(" ", 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return None
        token = parts[1].strip()
        if not token:
            return None

        config_manager = request.app.state.config_manager
        key = config_manager.find_auth_key(token)
        if key is not None:
            return key.id
        return None
