"""Pytest fixtures for integration tests against a live Docker Compose stack.

The stack (docker-compose.test.yml) must be running before tests execute.
The session-scoped ``wait_for_gateway`` fixture polls the health endpoint
until SearXNG is reported healthy, failing fast if the stack is unreachable.
"""

from __future__ import annotations

import contextlib
import os
import time

import httpx
import pytest
from dotenv import load_dotenv

load_dotenv()

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8080")
AUTH_TOKEN = os.environ.get("TEST_AUTH_TOKEN", "test-agent-key")
ADMIN_TOKEN = os.environ.get("TEST_ADMIN_TOKEN", "test-admin-key")

# Minimum delay (seconds) between tests that hit a given cloud provider.
# Keeps us under upstream rate limits without slowing down self-hosted tests.
_RATE_LIMIT_DELAYS: dict[str, float] = {
    "brave": 1.2,      # 1 req/sec
    "tavily": 0.5,     # 100 RPM
    "firecrawl": 0.5,  # 10+ RPM
    "jina": 0.3,       # 200 RPM with key
    "perplexity": 1.2, # Tier 0: 50 RPM
    "exa": 0.1,        # 10 QPS, minimal delay
    "flaresolverr": 3.0,  # Slow: challenge solving takes 5-30s
    "duckduckgo": 1.0,    # DDG rate-limits aggressive querying
}


@pytest.fixture(autouse=True)
def _cloud_rate_limit_delay(request: pytest.FixtureRequest):
    """Sleep before tests targeting rate-limited cloud providers."""
    module = request.module.__name__ if request.module else ""
    for provider, delay in _RATE_LIMIT_DELAYS.items():
        if provider in module:
            time.sleep(delay)
            break
    yield


@pytest.fixture(scope="session", autouse=True)
def wait_for_gateway() -> None:
    """Block until the gateway reports SearXNG as healthy."""
    deadline = time.time() + 90
    last_error = ""
    while time.time() < deadline:
        try:
            r = httpx.get(f"{GATEWAY_URL}/health", timeout=5)
            if r.status_code == 200:
                providers = r.json().get("providers", [])
                searxng = next(
                    (p for p in providers if p["name"] == "searxng"), None
                )
                if searxng and searxng["healthy"]:
                    return
                last_error = "SearXNG not healthy yet"
            else:
                last_error = f"health returned {r.status_code}"
        except (httpx.ConnectError, httpx.ReadTimeout) as exc:
            last_error = str(exc)
        time.sleep(2)

    pytest.fail(f"Gateway did not become ready: {last_error}")


@pytest.fixture(scope="session")
def gateway_url() -> str:
    return GATEWAY_URL


@pytest.fixture()
def client() -> httpx.Client:
    with httpx.Client(base_url=GATEWAY_URL, timeout=30) as c:
        yield c


@pytest.fixture()
def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {AUTH_TOKEN}"}


@pytest.fixture()
def admin_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN_TOKEN}"}


@pytest.fixture()
def jina_available(client: httpx.Client) -> None:
    """Skip tests only if Jina isn't registered or healthy on the gateway.

    Jina Reader works without an API key (20 RPM free tier), so we don't
    gate on JINA_API_KEY — only on whether the gateway can reach r.jina.ai.
    """
    r = client.get("/health")
    providers = r.json().get("providers", [])
    jina = next((p for p in providers if p["name"] == "jina"), None)
    if not jina or not jina["healthy"]:
        pytest.skip("Jina not healthy on gateway — skipping Jina tests")


@pytest.fixture(scope="session")
def brave_available() -> None:
    """Skip tests when Brave isn't healthy on the gateway.

    Brave enforces a 1 req/sec rate limit, so the health check can
    transiently report unhealthy.  We retry once after a short delay
    before deciding to skip.
    """
    for attempt in range(2):
        r = httpx.get(f"{GATEWAY_URL}/health", timeout=10)
        providers = r.json().get("providers", [])
        brave = next((p for p in providers if p["name"] == "brave"), None)
        if brave and brave["healthy"]:
            return
        if attempt == 0:
            time.sleep(2)

    pytest.skip("Brave not healthy on gateway — skipping Brave tests")


@pytest.fixture(scope="session")
def tavily_available() -> None:
    """Skip tests when Tavily isn't healthy on the gateway."""
    r = httpx.get(f"{GATEWAY_URL}/health", timeout=10)
    providers = r.json().get("providers", [])
    tavily = next((p for p in providers if p["name"] == "tavily"), None)
    if not tavily or not tavily["healthy"]:
        pytest.skip("Tavily not healthy on gateway — skipping Tavily tests")


@pytest.fixture(scope="session")
def firecrawl_available() -> None:
    """Skip tests when Firecrawl isn't healthy on the gateway."""
    r = httpx.get(f"{GATEWAY_URL}/health", timeout=15)
    providers = r.json().get("providers", [])
    firecrawl = next((p for p in providers if p["name"] == "firecrawl"), None)
    if not firecrawl or not firecrawl["healthy"]:
        pytest.skip("Firecrawl not healthy on gateway — skipping Firecrawl tests")


@pytest.fixture(scope="session")
def firecrawl_selfhosted_available() -> None:
    """Skip tests when self-hosted Firecrawl isn't running."""
    r = httpx.get(f"{GATEWAY_URL}/health", timeout=15)
    providers = r.json().get("providers", [])
    fc = next(
        (p for p in providers if p["name"] == "firecrawl_selfhosted"), None
    )
    if not fc or not fc["healthy"]:
        pytest.skip(
            "Firecrawl self-hosted not healthy — "
            "start with: docker compose -f docker-compose.test.yml "
            "--profile firecrawl-selfhosted up -d"
        )


INVISIBLE_PLAYWRIGHT_URL = os.environ.get(
    "INVISIBLE_PLAYWRIGHT_URL", "http://localhost:3001"
)


@pytest.fixture(scope="session")
def invisible_playwright_available() -> None:
    """Skip tests when invisible_playwright sidecar isn't running."""
    try:
        r = httpx.get(f"{INVISIBLE_PLAYWRIGHT_URL}/health", timeout=5)
        if r.status_code != 200:
            pytest.skip("invisible_playwright sidecar not healthy")
    except httpx.ConnectError:
        pytest.skip(
            "invisible_playwright sidecar not reachable — "
            "start with: docker compose -f docker-compose.test.yml "
            "-f docker-compose.invisible-playwright.yml up -d --build"
        )


@pytest.fixture()
def ipw_client() -> httpx.Client:
    return httpx.Client(base_url=INVISIBLE_PLAYWRIGHT_URL, timeout=60)


@contextlib.contextmanager
def _provider_skip_fixture(name: str, label: str | None = None):
    """Yield a session-scoped fixture that skips tests when *name* is unhealthy."""
    # This is a helper — fixtures are registered below.
    yield


def _make_provider_skip_fixture(name: str, human_label: str | None = None):
    """Factory for a session-scoped skip fixture."""
    label = human_label or name.title()

    @pytest.fixture(scope="session")
    def _skip_if_unhealthy() -> None:
        r = httpx.get(f"{GATEWAY_URL}/health", timeout=10)
        providers = r.json().get("providers", [])
        found = next((p for p in providers if p["name"] == name), None)
        if not found or not found["healthy"]:
            pytest.skip(f"{label} not healthy on gateway — skipping {name} tests")

    return _skip_if_unhealthy


# Register auto-skip fixtures for new providers
context7_available = _make_provider_skip_fixture("context7", "Context7")
perplexity_available = _make_provider_skip_fixture("perplexity", "Perplexity")
devdocs_available = _make_provider_skip_fixture("devdocs", "DevDocs")
exa_available = _make_provider_skip_fixture("exa", "Exa")
crawl4ai_available = _make_provider_skip_fixture("crawl4ai", "Crawl4AI")
crawl4ai_md_available = _make_provider_skip_fixture("crawl4ai_md", "Crawl4AI MD")
flaresolverr_available = _make_provider_skip_fixture("flaresolverr", "FlareSolverr")
zyte_available = _make_provider_skip_fixture("zyte", "Zyte")
duckduckgo_available = _make_provider_skip_fixture("duckduckgo", "DuckDuckGo")
