"""Integration tests for rate limiting.

Requires rate limiting to be enabled in config.test.yaml.
These tests hit the live Docker Compose stack.
"""

from __future__ import annotations

import httpx
import pytest

GATEWAY_URL = "http://localhost:8080"


@pytest.fixture(scope="module")
def client() -> httpx.Client:
    return httpx.Client(base_url=GATEWAY_URL, timeout=30)


def test_rate_limit_returns_429(client: httpx.Client, auth_headers: dict[str, str]):
    """Sending requests rapidly should eventually trigger a 429."""
    payload = {"query": "test rate limiting", "num_results": 1}
    statuses: list[int] = []
    for _ in range(20):
        r = client.post("/search", json=payload, headers=auth_headers)
        statuses.append(r.status_code)
        if r.status_code == 429:
            break

    assert 429 in statuses, (
        f"Expected at least one 429 among: {statuses}. "
        "Is rate limiting enabled in config.test.yaml?"
    )
    body = r.json()
    assert "detail" in body


def test_rate_limit_retry_after_header(
    client: httpx.Client, auth_headers: dict[str, str]
):
    """A 429 response should include a Retry-After header."""
    payload = {"query": "test retry after", "num_results": 1}
    for _ in range(30):
        r = client.post("/search", json=payload, headers=auth_headers)
        if r.status_code == 429:
            assert "retry-after" in r.headers
            break
