"""Provider adapter base interface, options, results, and metadata.

All adapters implement the ProviderAdapter protocol. The method for URL content
extraction is named ``extract`` (not ``scrape``) per the naming convention:
documentation may say "scrape", but tool calls and API endpoints use "extract".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Options passed to adapter calls
# ---------------------------------------------------------------------------


@dataclass
class SearchOptions:
    num_results: int = 10
    proxy_url: str | None = None
    timeout: int = 15


@dataclass
class ExtractOptions:
    format: str = "markdown"  # markdown | html | json
    proxy_url: str | None = None
    wait_for_selector: str | None = None
    session_cookies: dict[str, str] | None = None
    session_id: str | None = None
    fingerprint_id: str | None = None
    user_agent: str | None = None
    timeout: int = 15


# ---------------------------------------------------------------------------
# Normalised results returned by adapters
# ---------------------------------------------------------------------------


@dataclass
class ResultItem:
    title: str
    url: str
    snippet: str = ""
    published_date: str | None = None


@dataclass
class SearchResult:
    results: list[ResultItem] = field(default_factory=list)


@dataclass
class ExtractResult:
    content: str = ""
    format: str = "markdown"
    url: str = ""
    title: str | None = None
    status_code: int = 200


# ---------------------------------------------------------------------------
# Provider metadata (exposed via GET /providers)
# ---------------------------------------------------------------------------


@dataclass
class ProviderMetadata:
    name: str
    self_hosted: bool = False
    data_retention_days: int | None = None
    trains_on_queries: bool | None = None
    gdpr_compliant: bool = False
    hipaa_compliant: bool = False
    data_residency: list[str] = field(default_factory=lambda: ["unknown"])
    privacy_policy_url: str | None = None
    mcp_native: bool = False
    capabilities: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stealth: bool = False
    engine: str | None = None
    firefox_version: str | None = None
    specialization: str | None = None
    cost_units_per_call: float = 1.0


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ProviderError(Exception):
    """Raised by an adapter when the upstream provider fails."""

    def __init__(
        self,
        provider: str,
        message: str,
        *,
        status_code: int | None = None,
        error_class: str | None = None,
    ):
        self.provider = provider
        self.status_code = status_code
        self.error_class = error_class
        super().__init__(f"[{provider}] {message}")


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class ProviderAdapter(Protocol):
    """Uniform interface every provider adapter must satisfy.

    Whether the upstream speaks REST, MCP, or something else, the gateway
    only ever calls these two methods.
    """

    @property
    def name(self) -> str: ...

    @property
    def metadata(self) -> ProviderMetadata: ...

    async def search(
        self, query: str, options: SearchOptions
    ) -> SearchResult: ...

    async def extract(
        self, url: str, options: ExtractOptions
    ) -> ExtractResult: ...

    async def health_check(self) -> bool: ...
