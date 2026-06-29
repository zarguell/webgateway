"""Tests for SSRF protection URL validator."""

import pytest

from serp_llm.security.url_validator import (
    UrlValidationError,
    is_safe_url,
    validate_url,
)


class TestValidateUrl:
    def test_https_url_passes(self):
        validate_url("https://www.example.com/article")

    def test_http_url_passes(self):
        validate_url("http://example.com/page")

    def test_blocked_scheme_raises(self):
        with pytest.raises(UrlValidationError, match="Disallowed scheme"):
            validate_url("file:///etc/passwd")
        with pytest.raises(UrlValidationError, match="Disallowed scheme"):
            validate_url("ftp://files.example.com/file")
        with pytest.raises(UrlValidationError, match="Disallowed scheme"):
            validate_url("data:text/plain,hello")

    def test_localhost_hostname_blocked(self):
        with pytest.raises(UrlValidationError, match="blocked"):
            validate_url("http://localhost:8080/admin/")
        with pytest.raises(UrlValidationError, match="blocked"):
            validate_url("http://127.0.0.1/")
        with pytest.raises(UrlValidationError, match="blocked"):
            validate_url("http://0.0.0.0/")

    def test_private_ip_resolution_blocked(self):
        with pytest.raises(UrlValidationError, match="private/reserved"):
            validate_url("http://10.0.0.1/")
        with pytest.raises(UrlValidationError, match="private/reserved"):
            validate_url("http://192.168.1.1/")
        with pytest.raises(UrlValidationError, match="private/reserved"):
            validate_url("http://172.16.0.1/")

    def test_metadata_endpoints_blocked(self):
        with pytest.raises(UrlValidationError, match=r"(internal|private/reserved)"):
            validate_url("http://169.254.169.254/latest/meta-data/")
        with pytest.raises(UrlValidationError, match="internal metadata"):
            validate_url("http://instance-data.internal/")

    def test_is_safe_url_returns_bool(self):
        assert is_safe_url("https://example.com") is True
        assert is_safe_url("http://localhost/") is False
        assert is_safe_url("ftp://bad.com/") is False

    def test_ipv6_loopback_blocked(self):
        with pytest.raises(UrlValidationError, match="blocked"):
            validate_url("http://[::1]/")
