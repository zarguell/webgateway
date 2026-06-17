"""Regex-based DLP scanner.

Compiles rule patterns once at construction, then scans arbitrary text
for matches. Each match carries the rule that fired and the action to
take. Callers (the middleware layer) decide how to act on results.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from webgateway.dlp.luhn import is_valid_luhn

if TYPE_CHECKING:
    from webgateway.config import DLPRule


@dataclass
class DlpMatch:
    """A single detection hit."""

    rule_name: str
    action: str
    severity: str
    match_count: int
    sample: str
    replacement: str
    reroute_to: str | None


@dataclass
class DlpScanResult:
    """Aggregated result of scanning text against a set of rules."""

    matches: list[DlpMatch]
    redacted_text: str | None = None

    @property
    def has_block(self) -> bool:
        return any(m.action == "block" for m in self.matches)

    @property
    def has_reroute(self) -> bool:
        return any(m.action == "reroute" for m in self.matches)

    @property
    def reroute_target(self) -> str | None:
        for m in self.matches:
            if m.action == "reroute" and m.reroute_to:
                return m.reroute_to
        return None

    @property
    def highest_severity(self) -> str | None:
        order = {"critical": 4, "high": 3, "medium": 2, "low": 1}
        best = 0
        label = None
        for m in self.matches:
            r = order.get(m.severity, 0)
            if r > best:
                best = r
                label = m.severity
        return label


class DlpScanner:
    """Compiled regex scanner for a set of DLP rules.

    Patterns are compiled once at construction. The scanner is stateless
    after construction and safe to call concurrently.
    """

    def __init__(self, rules: list[DLPRule]) -> None:
        self._compiled: list[tuple[DLPRule, re.Pattern[str]]] = []
        for rule in rules:
            try:
                pat = re.compile(rule.pattern)
            except re.error:
                continue
            self._compiled.append((rule, pat))

    def scan(self, text: str) -> DlpScanResult:
        """Scan *text* against all compiled rules.

        Returns a :class:`DlpScanResult` with all matches. If any match
        has ``action == "redact"``, ``redacted_text`` contains the text
        with all redactable matches replaced.
        """
        matches: list[DlpMatch] = []
        redacted = text

        for rule, pat in self._compiled:
            found = pat.findall(redacted)
            if not found:
                continue

            if rule.validate_luhn:
                valid = [m for m in found if is_valid_luhn(m if isinstance(m, str) else "")]
                if not valid:
                    continue
                found = valid

            sample_raw = found[0] if isinstance(found[0], str) else str(found[0])
            sample = sample_raw[:40]

            matches.append(
                DlpMatch(
                    rule_name=rule.name or rule.pattern[:30],
                    action=rule.action,
                    severity=rule.severity,
                    match_count=len(found),
                    sample=sample,
                    replacement=rule.replacement,
                    reroute_to=rule.reroute_to,
                )
            )

            if rule.action == "redact":
                redacted = pat.sub(rule.replacement, redacted)

        return DlpScanResult(
            matches=matches,
            redacted_text=redacted if redacted != text else None,
        )
