# SSRF Protection & URL Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent Server-Side Request Forgery attacks by validating all user-supplied URLs before they reach provider adapters — including private IP blocking, URL format validation, and schema restriction.

**Architecture:** A `URLValidator` module that checks URLs against a private-IP blocklist (RFC 1918, loopback, link-local, etc.) and validates the URL scheme. Integrated into the `GatewayService.extract()` method before any provider dispatch. Pydantic `HttpUrl` constraint on `ExtractRequest.url` for early schema validation.

**Tech Stack:** Python `urllib`, `ipaddress`, Pydantic `HttpUrl`, custom blocklist.

---

### Task 1: URL validator module

**Files:**
- Create: `src/webgateway/security/__init__.py`
- Create: `src/webgateway/security/url_validator.py`
- Test: `tests/unit/test_url_validator.py`

- [ ] **Step 1: Create security package**

```bash
mkdir -p src/webgateway/security
```

Create `src/webgateway/security/__init__.py`:

```python
"""Security utilities: URL validation, SSRF protection, input sanitization."""
```

Create `src/webgateway/security/url_validator.py`:

```python
"""SSRF protection via URL validation and private-IP blocklisting.

Validates that user-supplied URLs:
1. Have an allowed scheme (http/https only)
2. Resolve to a public IP address (not RFC 1918, loopback, link-local, etc.)
3. Do not contain internal hostnames (localhost, metadata endpoints)
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Private and reserved IP ranges that should never be targeted by extraction
_PRIVATE_NETWORKS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    ipaddress.ip_network("127.0.0.0/8"),       # Loopback
    ipaddress.ip_network("10.0.0.0/8"),         # RFC 1918
    ipaddress.ip_network("172.16.0.0/12"),      # RFC 1918
    ipaddress.ip_network("192.168.0.0/16"),     # RFC 1918
    ipaddress.ip_network("169.254.0.0/16"),     # Link-local
    ipaddress.ip_network("0.0.0.0/8"),          # Current network
    ipaddress.ip_network("100.64.0.0/10"),      # Carrier-grade NAT (RFC 6598)
    ipaddress.ip_network("198.18.0.0/15"),      # Benchmarking (RFC 2544)
    ipaddress.ip_network("::1/128"),            # IPv6 loopback
    ipaddress.ip_network("fe80::/10"),          # IPv6 link-local
    ipaddress.ip_network("fc00::/7"),           # IPv6 unique local
]

# Hostnames that resolve to loopback or are clearly internal
_BLOCKED_HOSTNAMES: set[str] = {
    "localhost",
    "localhost.localdomain",
    "127.0.0.1",
    "::1",
    "0.0.0.0",
}

# Cloud metadata endpoints that are common SSRF targets
_METADATA_HOST_SUFFIXES: set[str] = {
    ".internal",
    ".compute.internal",
    ".ec2.internal",
}

_ALLOWED_SCHEMES = {"http", "https"}


class UrlValidationError(ValueError):
    """Raised when a URL fails SSRF validation."""

    def __init__(self, url: str, reason: str) -> None:
        self.url = url
        self.reason = reason
        super().__init__(f"URL validation failed for {url!r}: {reason}")


@dataclass(frozen=True)
class ValidationResult:
    """Result of URL validation."""
    valid: bool
    reason: str = ""


def validate_url(url: str) -> None:
    """Validate a URL for SSRF safety.

    Raises ``UrlValidationError`` if the URL is invalid, points to a private
    or reserved IP, uses a disallowed scheme, or appears to target an internal
    metadata endpoint.
    """
    parsed = urlparse(url)

    # 1. Scheme check
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise UrlValidationError(
            url,
            f"Disallowed scheme {parsed.scheme!r} (only http/https allowed)",
        )

    hostname = parsed.hostname or ""

    # 2. Blocked hostnames
    if hostname.lower() in _BLOCKED_HOSTNAMES:
        raise UrlValidationError(url, f"Hostname {hostname} is blocked")

    # 3. Metadata endpoint detection
    for suffix in _METADATA_HOST_SUFFIXES:
        if hostname.lower().endswith(suffix):
            raise UrlValidationError(
                url, f"Hostname {hostname} appears to be an internal metadata endpoint"
            )

    # 4. DNS resolution + IP range check
    _validate_ip(hostname, url)


def _validate_ip(hostname: str, url: str) -> None:
    """Resolve *hostname* to IP(s) and check against private ranges."""
    try:
        addrinfo = socket.getaddrinfo(hostname, 80)
    except (socket.gaierror, OSError) as exc:
        raise UrlValidationError(
            url, f"Could not resolve hostname {hostname}: {exc}"
        ) from exc

    for family, _, _, _, sockaddr in addrinfo:
        ip_str = sockaddr[0]
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            continue

        for network in _PRIVATE_NETWORKS:
            if addr in network:
                raise UrlValidationError(
                    url,
                    f"Resolved IP {ip_str} is in private/reserved range {network}",
                )


def is_safe_url(url: str) -> bool:
    """Return True if the URL passes SSRF validation, False otherwise.

    Use this for non-critical paths where you want to log a warning
    but not block the request.
    """
    try:
        validate_url(url)
        return True
    except UrlValidationError:
        return False
```

- [ ] **Step 2: Write URL validator tests**

Create `tests/unit/test_url_validator.py`:

```python
"""Tests for SSRF protection URL validator."""

import pytest

from webgateway.security.url_validator import (
    UrlValidationError,
    is_safe_url,
    validate_url,
)


class TestValidateUrl:
    """Tests for validate_url()."""

    def test_https_url_passes(self):
        """Standard HTTPS URL should pass validation."""
        # This should work — www.example.com is a public domain
        validate_url("https://www.example.com/article")

    def test_http_url_passes(self):
        """Standard HTTP URL should pass validation."""
        validate_url("http://example.com/page")

    def test_blocked_scheme_raises(self):
        """ftp, file, data etc. schemes should be rejected."""
        with pytest.raises(UrlValidationError, match="Disallowed scheme"):
            validate_url("file:///etc/passwd")
        with pytest.raises(UrlValidationError, match="Disallowed scheme"):
            validate_url("ftp://files.example.com/file")
        with pytest.raises(UrlValidationError, match="Disallowed scheme"):
            validate_url("data:text/plain,hello")

    def test_localhost_hostname_blocked(self):
        """localhost hostname should be rejected."""
        with pytest.raises(UrlValidationError, match="blocked"):
            validate_url("http://localhost:8080/admin/")
        with pytest.raises(UrlValidationError, match="blocked"):
            validate_url("http://127.0.0.1/")
        with pytest.raises(UrlValidationError, match="blocked"):
            validate_url("http://0.0.0.0/")

    def test_private_ip_resolution_blocked(self):
        """URLs that resolve to RFC 1918 addresses should be rejected."""
        # 10.x.x.x is RFC 1918
        with pytest.raises(UrlValidationError, match="private/reserved"):
            validate_url("http://10.0.0.1/")
        with pytest.raises(UrlValidationError, match="private/reserved"):
            validate_url("http://192.168.1.1/")
        with pytest.raises(UrlValidationError, match="private/reserved"):
            validate_url("http://172.16.0.1/")

    def test_metadata_endpoints_blocked(self):
        """AWS/GCP metadata endpoints should be rejected."""
        with pytest.raises(UrlValidationError, match="internal"):
            validate_url("http://169.254.169.254/latest/meta-data/")
        with pytest.raises(UrlValidationError, match="internal metadata"):
            validate_url("http://instance-data.internal/")

    def test_is_safe_url_returns_bool(self):
        """is_safe_url returns True/False without raising."""
        assert is_safe_url("https://example.com") is True
        assert is_safe_url("http://localhost/") is False
        assert is_safe_url("ftp://bad.com/") is False

    def test_docker_internal_hostnames_blocked(self):
        """Internal Docker service hostnames should be caught by resolution."""
        with pytest.raises(UrlValidationError):
            validate_url("http://searxng:8080/")


class TestValidateUrlEdgeCases:
    """Edge cases for URL validation."""

    def test_empty_url_raises(self):
        with pytest.raises((UrlValidationError, ValueError)):
            validate_url("")

    def test_url_without_scheme(self):
        with pytest.raises(UrlValidationError, match="Disallowed scheme"):
            validate_url("example.com/page")

    def test_ipv6_loopback_blocked(self):
        with pytest.raises(UrlValidationError, match="private/reserved"):
            validate_url("http://[::1]/")

    def test_url_with_credentials(self):
        """URLs with embedded credentials should still parse correctly."""
        with pytest.raises(UrlValidationError, match="blocked"):
            validate_url("http://user:pass@localhost/admin")
```

- [ ] **Step 3: Run validator tests**

```bash
source .venv/bin/activate && pytest tests/unit/test_url_validator.py -v
```

Expected: PASS (note: `searxng:8080` resolution test will likely fail in unit test context since `searxng` won't resolve — adjust the test to expect a resolution error instead of a blocklist hit if needed)

- [ ] **Step 4: Commit**

```bash
git add src/webgateway/security/ tests/unit/test_url_validator.py
git commit -m "feat(security): SSRF URL validator with private IP blocklist"
```

---

### Task 2: Add HttpUrl validation to ExtractRequest

**Files:**
- Modify: `src/webgateway/schemas.py`

- [ ] **Step 1: Update ExtractRequest.url to use HttpUrl**

In `src/webgateway/schemas.py`, add the import at the top (replacing the existing `from pydantic import BaseModel, Field`):

```python
from pydantic import BaseModel, Field, HttpUrl
```

Change the `ExtractRequest.url` field (line 62) from:

```python
class ExtractRequest(BaseModel):
    url: str
```

to:

```python
class ExtractRequest(BaseModel):
    url: HttpUrl
```

Note: `HttpUrl` in Pydantic v2 validates that the string is a valid HTTP or HTTPS URL with a hostname. It does NOT validate IP ranges — that's handled by the URL validator in Task 3.

- [ ] **Step 2: Update SearchRequest ResultItem.url (cosmetic)**

The `SearchResultItem.url` field (line 41) should remain `str` — search results come from providers, not users — so no change needed there.

- [ ] **Step 3: Verify Pydantic validation works**

Quick test from the Python shell:

```bash
source .venv/bin/activate && python -c "
from pydantic import HttpUrl
# Should work
u = HttpUrl('https://example.com/page')
print('Valid URL:', u)
# Should fail
try:
    HttpUrl('not-a-url')
except Exception as e:
    print('Caught:', e)
"
```

Expected output:
```
Valid URL: https://example.com/page
Caught: 1 validation error for HttpUrl
...
```

- [ ] **Step 4: Commit**

```bash
git add src/webgateway/schemas.py
git commit -m "feat(schemas): use HttpUrl type for ExtractRequest.url"
```

---

### Task 3: Wire URL validation into the service layer

**Files:**
- Modify: `src/webgateway/service.py` (add URL validation before provider dispatch)

- [ ] **Step 1: Add URL validation call in service.extract()**

In `src/webgateway/service.py`, find the `extract` method. It starts around line 529. Insert a URL validation call at the beginning of the method, before any provider dispatch:

```python
    async def extract(
        self,
        body: ExtractRequest,
        api_key_id: str = "unknown",
        dry_run: bool = False,
    ) -> ExtractResponse | DryRunResponse:
        """Extract content from a URL."""
        # --- SSRF check: validate the URL before any provider dispatch ---
        from webgateway.security.url_validator import validate_url

        try:
            validate_url(str(body.url))
        except ValueError as exc:
            raise ProviderError(
                provider="gateway",
                status_code=400,
                error_class="bad_request",
                message=str(exc),
            ) from exc

        # ... rest of the method continues ...
```

Note: `body.url` will be a `pydantic.HttpUrl` object after the schema change, so we need `str(body.url)` to get the string.

Add `ProviderError` to the import if it's already imported (it is — line 39-42):

```python
from webgateway.providers.base import (
    ExtractOptions,
    ProviderError,
    SearchOptions,
)
```

- [ ] **Step 2: Write integration-level test for blocked URL**

Add to `tests/unit/test_url_validator.py`:

```python
def test_gateway_rejects_private_url():
    """Simulate what the service layer does: validate_url on extract."""
    from webgateway.security.url_validator import validate_url

    with pytest.raises(UrlValidationError, match="blocked|private/reserved"):
        validate_url("http://192.168.1.1/config")
```

- [ ] **Step 3: Verify existing tests still pass**

```bash
source .venv/bin/activate && pytest tests/unit/ -x -q
```

Expected: all pass (or only pre-existing failures)

- [ ] **Step 4: Commit**

```bash
git add src/webgateway/service.py tests/unit/test_url_validator.py
git commit -m "feat(service): wire SSRF URL validation before extract dispatch"
```

---

### Task 4: Configuration docs update

**Files:**
- Modify: `docs-src/docs/configuration/config-yaml.md`

- [ ] **Step 1: Document the URL validation behavior**

Add a note to the config-yaml reference (or to the proxy section since it's adjacent):

In `docs-src/docs/configuration/config-yaml.md`, under the `providers` section or as a new subsection:

```markdown
### SSRF Protection (always active)

User-supplied URLs in `POST /extract` are validated for SSRF safety before any
provider dispatch. The validator:

- Rejects non-http/https schemes (file://, ftp://, data:, etc.)
- Blocks hostnames that resolve to private/reserved IP ranges (RFC 1918,
  loopback, link-local, carrier-grade NAT)
- Blocks known metadata endpoints (AWS/GCP internal hostnames)
- Uses `HttpUrl` pydantic validation for URL format

This protection is always active and does not require configuration.
```

- [ ] **Step 2: Commit**

```bash
git add docs-src/docs/configuration/config-yaml.md
git commit -m "docs: document SSRF protection URL validation"
```
