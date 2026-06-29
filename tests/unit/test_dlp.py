"""Unit tests for the DLP module: Luhn validator, scanner, middleware."""

from __future__ import annotations

from serp_llm.config import DLPPolicy, DLPRule
from serp_llm.dlp.luhn import is_valid_luhn
from serp_llm.dlp.middleware import DlpBlockedError, DlpMiddleware
from serp_llm.dlp.scanner import DlpMatch, DlpScanner

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _policy(
    name: str = "p",
    outbound_rules: list[DLPRule] | None = None,
    inbound_rules: list[DLPRule] | None = None,
    applies_to_providers: list[str] | None = None,
    enabled: bool = True,
) -> dict:
    """Build a policy dict for DlpMiddleware.

    model_dump() is called for the top-level policy fields, but the rule
    lists are restored to DLPRule objects because DlpScanner accesses
    rule attributes (rule.pattern) rather than dict keys.
    """
    policy = DLPPolicy(
        name=name,
        enabled=enabled,
        applies_to_providers=applies_to_providers or [],
        outbound_rules=outbound_rules or [],
        inbound_rules=inbound_rules or [],
    )
    data = policy.model_dump()
    data["outbound_rules"] = policy.outbound_rules
    data["inbound_rules"] = policy.inbound_rules
    return data


# ---------------------------------------------------------------------------
# Luhn validator
# ---------------------------------------------------------------------------


class TestLuhnValidator:
    def test_valid_visa(self):
        assert is_valid_luhn("4111111111111111") is True

    def test_valid_mastercard(self):
        assert is_valid_luhn("5555555555554444") is True

    def test_invalid_checksum(self):
        assert is_valid_luhn("4111111111111112") is False

    def test_too_short(self):
        assert is_valid_luhn("123456789012") is False

    def test_too_long(self):
        assert is_valid_luhn("12345678901234567890") is False

    def test_with_separators(self):
        assert is_valid_luhn("4111-1111-1111-1111") is True

    def test_all_zeros(self):
        # 13 zeros pass length check; checksum 0 % 10 == 0.
        assert is_valid_luhn("0000000000000") is True

    def test_valid_amex(self):
        assert is_valid_luhn("378282246310005") is True


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


class TestDlpScanner:
    def test_no_matches_on_clean_text(self):
        scanner = DlpScanner([DLPRule(name="aws", pattern=r"AKIA[0-9A-Z]{16}")])
        result = scanner.scan("nothing suspicious here")
        assert result.matches == []

    def test_single_match(self):
        scanner = DlpScanner([DLPRule(name="aws-key", pattern=r"AKIA[0-9A-Z]{16}")])
        result = scanner.scan("found AKIAIOSFODNN7EXAMPLE in logs")
        assert len(result.matches) == 1
        assert result.matches[0].rule_name == "aws-key"
        assert result.matches[0].match_count == 1

    def test_multiple_matches_same_rule(self):
        scanner = DlpScanner([DLPRule(name="aws", pattern=r"AKIA[0-9A-Z]{16}")])
        text = "keys: AKIAIOSFODNN7EXAMPLE and AKIAI44JG2BQYXYZABCD"
        result = scanner.scan(text)
        assert len(result.matches) == 1
        assert result.matches[0].match_count == 2

    def test_multiple_rules_matching(self):
        rules = [
            DLPRule(name="aws", pattern=r"AKIA[0-9A-Z]{16}"),
            DLPRule(name="ssn", pattern=r"\d{3}-\d{2}-\d{4}", action="log"),
        ]
        scanner = DlpScanner(rules)
        result = scanner.scan("AKIAIOSFODNN7EXAMPLE ssn 123-45-6789")
        assert len(result.matches) == 2
        names = {m.rule_name for m in result.matches}
        assert names == {"aws", "ssn"}

    def test_redact_action_modifies_text(self):
        rule = DLPRule(
            name="ssn",
            pattern=r"\d{3}-\d{2}-\d{4}",
            action="redact",
            replacement="[SSN]",
        )
        scanner = DlpScanner([rule])
        result = scanner.scan("ssn=123-45-6789 end")
        assert result.redacted_text is not None
        assert "123-45-6789" not in result.redacted_text
        assert "[SSN]" in result.redacted_text

    def test_block_action_sets_has_block(self):
        rule = DLPRule(name="aws", pattern=r"AKIA[0-9A-Z]{16}", action="block")
        scanner = DlpScanner([rule])
        result = scanner.scan("AKIAIOSFODNN7EXAMPLE")
        assert result.has_block is True

    def test_reroute_action_sets_target(self):
        rule = DLPRule(
            name="cc",
            pattern=r"\b\d{13,19}\b",
            action="reroute",
            reroute_to="local-proxy",
        )
        scanner = DlpScanner([rule])
        result = scanner.scan("card=4111111111111111")
        assert result.has_reroute is True
        assert result.reroute_target == "local-proxy"

    def test_validate_luhn_filters_invalid(self):
        rule = DLPRule(
            name="cc",
            pattern=r"\b\d{13,19}\b",
            action="block",
            validate_luhn=True,
        )
        scanner = DlpScanner([rule])
        # 16 digits but fails Luhn checksum.
        result = scanner.scan("1234567890123456")
        assert result.matches == []

    def test_validate_luhn_keeps_valid(self):
        rule = DLPRule(
            name="cc",
            pattern=r"\b\d{13,19}\b",
            action="block",
            validate_luhn=True,
        )
        scanner = DlpScanner([rule])
        result = scanner.scan("4111111111111111")
        assert len(result.matches) == 1
        assert result.matches[0].rule_name == "cc"

    def test_invalid_regex_silently_skipped(self):
        rules = [
            DLPRule(name="broken", pattern=r"[", action="block"),
            DLPRule(name="aws", pattern=r"AKIA[0-9A-Z]{16}", action="block"),
        ]
        scanner = DlpScanner(rules)
        result = scanner.scan("AKIAIOSFODNN7EXAMPLE")
        assert len(result.matches) == 1
        assert result.matches[0].rule_name == "aws"

    def test_highest_severity(self):
        rules = [
            DLPRule(name="low", pattern=r"AKIA[0-9A-Z]{16}", severity="low"),
            DLPRule(name="crit", pattern=r"\d{3}-\d{2}-\d{4}", severity="critical"),
        ]
        scanner = DlpScanner(rules)
        result = scanner.scan("AKIAIOSFODNN7EXAMPLE 123-45-6789")
        assert result.highest_severity == "critical"

    def test_redacted_text_none_when_no_redact_rule(self):
        rule = DLPRule(name="aws", pattern=r"AKIA[0-9A-Z]{16}", action="block")
        scanner = DlpScanner([rule])
        result = scanner.scan("AKIAIOSFODNN7EXAMPLE")
        assert result.redacted_text is None


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class TestDlpMiddleware:
    def test_outbound_no_matching_policy(self):
        mw = DlpMiddleware([_policy(applies_to_providers=["openai"])])
        outcome = mw.check_outbound("sensitive text", "anthropic")
        assert outcome.action == "pass"

    def test_outbound_block(self):
        mw = DlpMiddleware([_policy(outbound_rules=[
            DLPRule(name="aws", pattern=r"AKIA[0-9A-Z]{16}", action="block"),
        ])])
        outcome = mw.check_outbound("AKIAIOSFODNN7EXAMPLE", "openai")
        assert outcome.action == "block"
        assert len(outcome.matches) == 1

    def test_outbound_redact(self):
        mw = DlpMiddleware([_policy(outbound_rules=[
            DLPRule(name="ssn", pattern=r"\d{3}-\d{2}-\d{4}",
                    action="redact", replacement="[SSN]"),
        ])])
        outcome = mw.check_outbound("ssn 123-45-6789", "openai")
        assert outcome.action == "redact"
        assert outcome.redacted_text is not None
        assert "123-45-6789" not in outcome.redacted_text

    def test_outbound_reroute(self):
        mw = DlpMiddleware([_policy(outbound_rules=[
            DLPRule(name="cc", pattern=r"\b\d{13,19}\b",
                    action="reroute", reroute_to="vault"),
        ])])
        outcome = mw.check_outbound("4111111111111111", "openai")
        assert outcome.action == "reroute"
        assert outcome.reroute_to == "vault"

    def test_outbound_clean_text_passes(self):
        mw = DlpMiddleware([_policy(outbound_rules=[
            DLPRule(name="aws", pattern=r"AKIA[0-9A-Z]{16}", action="block"),
        ])])
        outcome = mw.check_outbound("just a normal query", "openai")
        assert outcome.action == "pass"

    def test_outbound_log_action(self):
        mw = DlpMiddleware([_policy(outbound_rules=[
            DLPRule(name="aws", pattern=r"AKIA[0-9A-Z]{16}", action="log"),
        ])])
        outcome = mw.check_outbound("AKIAIOSFODNN7EXAMPLE", "openai")
        assert outcome.action == "log"
        assert len(outcome.matches) == 1

    def test_inbound_redact(self):
        mw = DlpMiddleware([_policy(inbound_rules=[
            DLPRule(name="ssn", pattern=r"\d{3}-\d{2}-\d{4}",
                    action="redact", replacement="[SSN]"),
        ])])
        outcome = mw.check_inbound("resp: 123-45-6789", "openai")
        assert outcome.action == "redact"
        assert outcome.redacted_text is not None

    def test_inbound_block(self):
        mw = DlpMiddleware([_policy(inbound_rules=[
            DLPRule(name="aws", pattern=r"AKIA[0-9A-Z]{16}", action="block"),
        ])])
        outcome = mw.check_inbound("leaked AKIAIOSFODNN7EXAMPLE", "openai")
        assert outcome.action == "block"

    def test_inbound_clean_text_passes(self):
        mw = DlpMiddleware([_policy(inbound_rules=[
            DLPRule(name="aws", pattern=r"AKIA[0-9A-Z]{16}", action="block"),
        ])])
        outcome = mw.check_inbound("clean response body", "openai")
        assert outcome.action == "pass"

    def test_provider_not_in_list_skipped(self):
        mw = DlpMiddleware([_policy(
            applies_to_providers=["openai"],
            outbound_rules=[
                DLPRule(name="aws", pattern=r"AKIA[0-9A-Z]{16}", action="block"),
            ],
        )])
        outcome = mw.check_outbound("AKIAIOSFODNN7EXAMPLE", "anthropic")
        assert outcome.action == "pass"

    def test_empty_applies_matches_all(self):
        mw = DlpMiddleware([_policy(
            applies_to_providers=[],
            outbound_rules=[
                DLPRule(name="aws", pattern=r"AKIA[0-9A-Z]{16}", action="block"),
            ],
        )])
        outcome = mw.check_outbound("AKIAIOSFODNN7EXAMPLE", "anything")
        assert outcome.action == "block"

    def test_disabled_policy_skipped(self):
        mw = DlpMiddleware([_policy(
            enabled=False,
            outbound_rules=[
                DLPRule(name="aws", pattern=r"AKIA[0-9A-Z]{16}", action="block"),
            ],
        )])
        outcome = mw.check_outbound("AKIAIOSFODNN7EXAMPLE", "openai")
        assert outcome.action == "pass"

    def test_first_matching_policy_wins(self):
        mw = DlpMiddleware([
            _policy(
                name="first",
                applies_to_providers=["openai"],
                outbound_rules=[
                    DLPRule(name="aws", pattern=r"AKIA[0-9A-Z]{16}",
                            action="block"),
                ],
            ),
            _policy(
                name="second",
                applies_to_providers=["openai"],
                outbound_rules=[
                    DLPRule(name="ssn", pattern=r"\d{3}-\d{2}-\d{4}",
                            action="redact"),
                ],
            ),
        ])
        outcome = mw.check_outbound(
            "AKIAIOSFODNN7EXAMPLE 123-45-6789", "openai",
        )
        assert outcome.action == "block"
        assert outcome.policy_name == "first"


# ---------------------------------------------------------------------------
# DlpBlockedError
# ---------------------------------------------------------------------------


class TestDlpBlockedError:
    def test_construction(self):
        match = DlpMatch(
            rule_name="aws-key",
            action="block",
            severity="critical",
            match_count=1,
            sample="AKIAIOSFODNN7EXAMPLE",
            replacement="[REDACTED]",
            reroute_to=None,
        )
        error = DlpBlockedError("strict-dlp", [match])
        assert error.policy == "strict-dlp"
        assert error.match_names == ["aws-key"]
        assert "strict-dlp" in str(error)
        assert "aws-key" in str(error)

    def test_is_exception(self):
        match = DlpMatch(
            rule_name="x",
            action="block",
            severity="low",
            match_count=1,
            sample="x",
            replacement="[R]",
            reroute_to=None,
        )
        error = DlpBlockedError(None, [match])
        assert isinstance(error, Exception)

    def test_empty_matches(self):
        error = DlpBlockedError("policy", [])
        assert error.policy == "policy"
        assert error.match_names == []
