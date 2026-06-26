"""Rate-limiting ASGI wrapper — wraps ``app.__call__`` at lifespan time.

Avoids ``app.add_middleware`` because that wraps the router, not the app,
making ``app.state`` inaccessible. Instead, we replace ``app.__call__``
with a closure that has access to ``app``.

Usage in a FastAPI lifespan::

    from webgateway.ratelimit.middleware import activate_rate_limiting

    rate_limiter = SlidingWindowRateLimiter(config)
    app.state.rate_limiter = rate_limiter
    await rate_limiter.start_background_cleanup()
    activate_rate_limiting(app)
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from fastapi import FastAPI
from starlette.responses import Response

from webgateway.ratelimit.limiter import RateLimitExceeded

logger = logging.getLogger(__name__)

_RATE_LIMITED_PATHS = ("/search", "/extract", "/mcp")


def activate_rate_limiting(app: FastAPI) -> None:
    """Wrap ``app.__call__`` with a rate-limit check.

    Must be called after ``app.state.rate_limiter`` has been set.
    """
    original_call: Callable = app.__call__

    async def _rate_limited_call(scope, receive, send) -> None:
        if scope["type"] == "http":
            limiter = getattr(app.state, "rate_limiter", None)
            if limiter is not None:
                config = app.state.config_manager.config.rate_limiting
                if config.enabled:
                    path = scope.get("path", "")
                    if any(path.startswith(p) for p in _RATE_LIMITED_PATHS):
                        checks: list[tuple[str, int, int]] = []

                        client = scope.get("client")
                        client_host = client[0] if client else "unknown"
                        checks.append((
                            f"ip:{client_host}",
                            config.by_ip.requests,
                            config.by_ip.window_seconds,
                        ))

                        scope_state = scope.get("state", {})
                        api_key_id: str | None = (
                            scope_state.get("api_key_id")
                            if isinstance(scope_state, dict)
                            else getattr(scope_state, "api_key_id", None)
                        )
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
                            body = (
                                f'{{"error":"rate_limit_exceeded",'
                                f'"detail":"{exc}",'
                                f'"retry_after":{exc.window_seconds}}}'
                            ).encode()
                            response = Response(
                                body,
                                status_code=429,
                                media_type="application/json",
                                headers={"Retry-After": str(exc.window_seconds)},
                            )
                            await response(scope, receive, send)
                            return

        await original_call(scope, receive, send)

    app.__call__ = _rate_limited_call
    logger.info("Rate limiting activated")
