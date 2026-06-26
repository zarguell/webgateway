"""Middleware that sets security-related HTTP response headers."""

from __future__ import annotations

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to every response.

    Headers set:
    - Strict-Transport-Security (HSTS) — 1 year, include subdomains (HTTPS only)
    - X-Content-Type-Options — nosniff
    - X-Frame-Options — DENY
    - Referrer-Policy — no-referrer
    - Permissions-Policy — restrict sensitive features
    - Content-Security-Policy — restrictive for API, relaxed for admin UI
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)

        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )

        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"

        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), interest-cohort=()"
        )

        if request.url.path.startswith("/admin"):
            csp = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' https://unpkg.com/htmx.org; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; "
                "connect-src 'self'; "
                "form-action 'self'"
            )
        else:
            csp = (
                "default-src 'self'; "
                "script-src 'none'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self'; "
                "form-action 'none'"
            )
        response.headers["Content-Security-Policy"] = csp

        return response
