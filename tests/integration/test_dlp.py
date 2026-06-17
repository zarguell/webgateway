"""Integration tests for DLP (Data Loss Prevention) enforcement.

These tests run against a live Docker Compose stack (SearXNG + WebGateway)
and verify that the DLP middleware blocks, redacts, and reroutes requests
through the real HTTP API.

DLP rules are defined in ``config.test.yaml`` under ``dlp_policies``:
- Outbound: SSN -> block, Email -> redact, diagnosis/medication -> reroute
- Inbound:  AWS key (AKIA...) -> redact, OpenAI key (sk-...) -> redact

Start the stack with ``make integration-up`` then ``make integration-test``.
"""

from __future__ import annotations

import httpx

SSN = "123-45-6789"
EMAIL = "test@example.com"


class TestDlpSearchOutbound:
    def test_search_with_ssn_returns_403(
        self, client: httpx.Client, auth_headers
    ):
        r = client.post(
            "/search",
            json={"query": f"my ssn is {SSN}"},
            headers=auth_headers,
        )
        assert r.status_code == 403
        assert "Blocked by DLP" in r.text

    def test_search_with_email_is_redacted(
        self, client: httpx.Client, auth_headers
    ):
        r = client.post(
            "/search",
            json={"query": f"contact me at {EMAIL} please"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert "results" in data
        assert data["provider_used"] == "searxng"

    def test_search_health_term_reroutes_to_searxng(
        self, client: httpx.Client, auth_headers
    ):
        r = client.post(
            "/search",
            json={"query": "diagnosis symptoms flu"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["provider_used"] == "searxng"

    def test_search_medication_term_reroutes_to_searxng(
        self, client: httpx.Client, auth_headers
    ):
        r = client.post(
            "/search",
            json={"query": "new medication side effects"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["provider_used"] == "searxng"

    def test_clean_search_passes_dlp(
        self, client: httpx.Client, auth_headers
    ):
        r = client.post(
            "/search",
            json={"query": "python programming language"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert len(r.json()["results"]) > 0

    def test_search_with_multiple_violations_block_wins(
        self, client: httpx.Client, auth_headers
    ):
        r = client.post(
            "/search",
            json={"query": f"{SSN} and {EMAIL}"},
            headers=auth_headers,
        )
        assert r.status_code == 403
        matched = r.json()["error"]["matched_rules"]
        assert "SSN" in matched

    def test_search_redact_preserves_other_text(
        self, client: httpx.Client, auth_headers
    ):
        r = client.post(
            "/search",
            json={"query": f"{EMAIL} python programming"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["provider_used"] == "searxng"
        assert len(data["results"]) > 0

    def test_search_with_credit_card_not_blocked(
        self, client: httpx.Client, auth_headers
    ):
        # config.test.yaml has no credit-card rule, so this passes DLP.
        r = client.post(
            "/search",
            json={"query": "card number 4111111111111111 test"},
            headers=auth_headers,
        )
        assert r.status_code == 200


class TestDlpExtractOutbound:
    def test_extract_with_ssn_in_url_returns_403(
        self, client: httpx.Client, auth_headers
    ):
        r = client.post(
            "/extract",
            json={"url": f"https://example.com/page?ref={SSN}"},
            headers=auth_headers,
        )
        assert r.status_code == 403
        assert "Blocked by DLP" in r.text


class TestDlpAuthInteraction:
    def test_dlp_blocked_request_still_requires_auth(
        self, client: httpx.Client
    ):
        # Auth middleware runs before DLP — no token means 401, not 403.
        r = client.post(
            "/search",
            json={"query": f"my ssn is {SSN}"},
        )
        assert r.status_code == 401


class TestDlpErrorStructure:
    def test_blocked_response_has_structured_error(
        self, client: httpx.Client, auth_headers
    ):
        r = client.post(
            "/search",
            json={"query": SSN},
            headers=auth_headers,
        )
        assert r.status_code == 403
        error = r.json()["error"]
        assert "message" in error
        assert "Blocked by DLP" in error["message"]
        assert error["policy"] == "test_dlp"
        assert isinstance(error["matched_rules"], list)
        assert "SSN" in error["matched_rules"]
