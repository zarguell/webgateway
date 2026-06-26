"""Tests for security headers middleware."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from webgateway.middleware.security_headers import SecurityHeadersMiddleware


def _make_app() -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.post("/search")
    async def search():
        return {"results": []}

    @app.get("/admin/dashboard")
    async def admin():
        return {"admin": True}

    app.add_middleware(SecurityHeadersMiddleware)
    return app


class TestSecurityHeaders:
    def _check_common_headers(self, resp):
        assert resp.headers.get("x-content-type-options") == "nosniff"
        assert resp.headers.get("x-frame-options") == "DENY"
        assert resp.headers.get("referrer-policy") == "no-referrer"
        assert "permissions-policy" in resp.headers

    def test_api_endpoint_has_headers(self):
        client = TestClient(_make_app())
        self._check_common_headers(client.get("/health"))

    def test_admin_endpoint_has_headers(self):
        client = TestClient(_make_app())
        self._check_common_headers(client.get("/admin/dashboard"))

    def test_api_endpoint_restrictive_csp(self):
        client = TestClient(_make_app())
        resp = client.get("/health")
        csp = resp.headers.get("content-security-policy", "")
        assert "script-src 'none'" in csp, f"Expected restrictive CSP, got: {csp}"

    def test_admin_endpoint_relaxed_csp(self):
        client = TestClient(_make_app())
        resp = client.get("/admin/dashboard")
        csp = resp.headers.get("content-security-policy", "")
        assert "htmx.org" in csp, f"Expected relaxed CSP with htmx, got: {csp}"

    def test_hsts_not_set_on_http(self):
        client = TestClient(_make_app())
        resp = client.get("/health")
        # TestClient uses http by default, so HSTS should NOT be set
        assert "strict-transport-security" not in resp.headers
