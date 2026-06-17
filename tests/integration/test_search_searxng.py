"""Integration tests for SearXNG search through the WebGateway.

These tests run against a live Docker Compose stack (SearXNG + WebGateway).
Start the stack with ``make integration-up`` then run ``make integration-test``,
or do both at once with ``make test-integration``.
"""

from __future__ import annotations

import httpx


class TestHealth:
    def test_health_returns_ok(self, client: httpx.Client):
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"

    def test_health_lists_searxng_as_healthy(self, client: httpx.Client):
        r = client.get("/health")
        providers = r.json()["providers"]
        searxng = next(p for p in providers if p["name"] == "searxng")
        assert searxng["healthy"] is True
        assert searxng["last_check_ts"] is not None


class TestSearchAuth:
    def test_search_without_auth_returns_401(self, client: httpx.Client):
        r = client.post("/search", json={"query": "test"})
        assert r.status_code == 401

    def test_search_with_wrong_token_returns_401(self, client: httpx.Client):
        r = client.post(
            "/search",
            json={"query": "test"},
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert r.status_code == 401

    def test_admin_endpoint_rejects_non_admin(self, client: httpx.Client, auth_headers):
        r = client.post("/admin/reload", headers=auth_headers)
        assert r.status_code == 403


class TestSearchExecution:
    def test_basic_search_returns_results(self, client: httpx.Client, auth_headers):
        r = client.post(
            "/search",
            json={"query": "python programming language", "num_results": 5},
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert len(data["results"]) > 0
        assert data["provider_used"] == "searxng"

    def test_search_result_items_have_required_fields(
        self, client: httpx.Client, auth_headers
    ):
        r = client.post(
            "/search",
            json={"query": "wikipedia python", "num_results": 3},
            headers=auth_headers,
        )
        assert r.status_code == 200
        results = r.json()["results"]
        assert len(results) > 0
        for item in results:
            assert "title" in item
            assert "url" in item
            assert "snippet" in item

    def test_search_response_has_metadata(self, client: httpx.Client, auth_headers):
        r = client.post(
            "/search",
            json={"query": "fastapi python", "num_results": 3},
            headers=auth_headers,
        )
        data = r.json()
        assert "provider_used" in data
        assert "request_id" in data
        assert "latency_ms" in data
        assert data["request_id"].startswith("req_")
        assert isinstance(data["latency_ms"], int)
        assert data["latency_ms"] > 0

    def test_search_request_id_in_response_header(
        self, client: httpx.Client, auth_headers
    ):
        r = client.post(
            "/search",
            json={"query": "httpx async python", "num_results": 1},
            headers=auth_headers,
        )
        assert r.status_code == 200
        request_id_header = r.headers.get("x-request-id")
        assert request_id_header is not None
        assert request_id_header.startswith("req_")
        assert request_id_header == r.json()["request_id"]

    def test_num_results_limits_output(self, client: httpx.Client, auth_headers):
        r = client.post(
            "/search",
            json={"query": "machine learning", "num_results": 2},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert len(r.json()["results"]) <= 2


class TestDryRun:
    def test_dry_run_returns_policy_decision(self, client: httpx.Client, auth_headers):
        r = client.post(
            "/search?dry_run=true",
            json={"query": "test query"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert "decision" in data
        assert "request_id" in data
        decision = data["decision"]
        assert decision["provider"] == "searxng"
        assert decision["retry_strategy"] == "fallback"

    def test_dry_run_does_not_execute_search(
        self, client: httpx.Client, auth_headers
    ):
        r = client.post(
            "/search?dry_run=true",
            json={"query": "zzz_nonexistent_xyz"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert "results" not in r.json()


class TestPolicyMatching:
    def test_reddit_domain_matches_policy(self, client: httpx.Client, auth_headers):
        r = client.post(
            "/extract?dry_run=true",
            json={"url": "https://www.reddit.com/r/python"},
            headers=auth_headers,
        )
        decision = r.json()["decision"]
        assert decision["policy_matched"] == "reddit_glob_test"

    def test_unknown_domain_uses_default(self, client: httpx.Client, auth_headers):
        r = client.post(
            "/extract?dry_run=true",
            json={"url": "https://example.com/page"},
            headers=auth_headers,
        )
        decision = r.json()["decision"]
        assert decision["policy_matched"] is None
        assert decision["provider"] == "jina"


class TestAdminReload:
    def test_reload_succeeds_with_admin_key(
        self, client: httpx.Client, admin_headers
    ):
        r = client.post("/admin/reload", headers=admin_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["reloaded"] is True
        assert len(data["config_hash"]) > 0


class TestProviderMetadata:
    def test_providers_endpoint_returns_searxng(
        self, client: httpx.Client, auth_headers
    ):
        r = client.get("/providers", headers=auth_headers)
        assert r.status_code == 200
        providers = r.json()
        assert len(providers) > 0
        searxng = next(p for p in providers if p["name"] == "searxng")
        assert searxng["self_hosted"] is True
        assert searxng["enabled"] is True
        assert "search" in searxng["capabilities"]
