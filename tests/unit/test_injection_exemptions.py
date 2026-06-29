from __future__ import annotations

from serp_llm.injection.exemptions import is_exempt


class TestExemptions:
    def test_exempt_domain_exact_match(self):
        assert is_exempt(
            url="https://docs.python.org/3/library",
            api_key_id="key1",
            exempt_domains=["docs.python.org"],
            exempt_api_key_ids=[],
        ) is True

    def test_exempt_domain_no_match(self):
        assert is_exempt(
            url="https://evil.com/exploit",
            api_key_id="key1",
            exempt_domains=["docs.python.org"],
            exempt_api_key_ids=[],
        ) is False

    def test_exempt_api_key_id(self):
        assert is_exempt(
            url="https://any.com/page",
            api_key_id="key_trusted",
            exempt_domains=[],
            exempt_api_key_ids=["key_trusted"],
        ) is True

    def test_exempt_api_key_id_no_match(self):
        assert is_exempt(
            url="https://any.com/page",
            api_key_id="key_regular",
            exempt_domains=[],
            exempt_api_key_ids=["key_trusted"],
        ) is False

    def test_no_exemptions_configured(self):
        assert is_exempt(
            url="https://any.com/page",
            api_key_id="key1",
            exempt_domains=[],
            exempt_api_key_ids=[],
        ) is False

    def test_exempt_domain_subdomain_not_matched(self):
        """Only exact domain match — subdomains are NOT exempt."""
        assert is_exempt(
            url="https://sub.docs.python.org/page",
            api_key_id="key1",
            exempt_domains=["docs.python.org"],
            exempt_api_key_ids=[],
        ) is False

    def test_exempt_domain_glob_pattern(self):
        """Wildcard domain patterns are supported."""
        assert is_exempt(
            url="https://sub.python.org/page",
            api_key_id="key1",
            exempt_domains=["*.python.org"],
            exempt_api_key_ids=[],
        ) is True
