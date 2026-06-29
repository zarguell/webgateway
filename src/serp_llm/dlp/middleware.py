"""High-level DLP enforcement layer.

Sits between the policy engine and the provider dispatch. Two phases:

- **Outbound**: scan the request payload (query text or URL) *before* it
  reaches the provider. May block, redact, or reroute the request.
- **Inbound**: scan the provider response content *before* it is returned
  to the caller. May redact secrets from the body.

The middleware is constructed once at startup from the config's
``dlp_policies`` list. Each policy is compiled into separate outbound
and inbound scanners. At request time, the matching policy is selected
based on the resolved provider name (``applies_to_providers``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from serp_llm.config import DLPRule
from serp_llm.dlp.luhn import is_valid_luhn  # noqa: F401  re-exported for tests
from serp_llm.dlp.scanner import DlpMatch, DlpScanner


class DlpBlockedError(Exception):
    """Raised when DLP policy blocks a request or response."""

    def __init__(self, policy: str | None, matches: list[DlpMatch]):
        self.policy = policy
        self.match_names = [m.rule_name for m in matches]
        super().__init__(
            f"Blocked by DLP policy '{policy}': {', '.join(self.match_names)}"
        )


@dataclass
class DlpOutcome:
    """Result of a single DLP enforcement pass."""

    action: str = "pass"  # pass | block | redact | reroute | log
    redacted_text: str | None = None
    reroute_to: str | None = None
    policy_name: str | None = None
    matches: list[DlpMatch] = field(default_factory=list)


class DlpMiddleware:
    """Owns compiled scanners for all configured DLP policies.

    Selecting the active policy for a request:

    1. If ``applies_to_providers`` is empty → applies to all providers.
    2. Otherwise → applies only when the resolved provider is in the list.
    3. First matching policy wins.
    """

    def __init__(self, policies_data: list[dict]) -> None:
        """Build middleware from raw policy dicts.

        Expects a list of validated ``DLPPolicy`` objects serialised to
        dicts (via ``model_dump()``). We accept raw dicts so the middleware
        can be constructed from config without importing Pydantic models.
        """
        self._policies: list[dict] = []
        self._outbound_scanners: list[DlpScanner | None] = []
        self._inbound_scanners: list[DlpScanner | None] = []

        for p in policies_data:
            if not p.get("enabled", True):
                self._policies.append(p)
                self._outbound_scanners.append(None)
                self._inbound_scanners.append(None)
                continue

            self._policies.append(p)

            out_rules = p.get("outbound_rules", [])
            in_rules = p.get("inbound_rules", [])
            if out_rules:
                out_rules = [
                    r if isinstance(r, DLPRule) else DLPRule(**r)
                    for r in out_rules
                ]
            if in_rules:
                in_rules = [
                    r if isinstance(r, DLPRule) else DLPRule(**r)
                    for r in in_rules
                ]
            self._outbound_scanners.append(
                DlpScanner(out_rules) if out_rules else None
            )
            self._inbound_scanners.append(
                DlpScanner(in_rules) if in_rules else None
            )

    def _select_policy_index(self, provider: str) -> int | None:
        for i, p in enumerate(self._policies):
            if not p.get("enabled", True):
                continue
            applies = p.get("applies_to_providers", [])
            if not applies or provider in applies:
                return i
        return None

    def check_outbound(self, text: str, provider: str) -> DlpOutcome:
        """Scan outbound payload (query/URL) before provider dispatch."""
        idx = self._select_policy_index(provider)
        if idx is None or self._outbound_scanners[idx] is None:
            return DlpOutcome()

        scanner = self._outbound_scanners[idx]  # type: ignore[assignment]
        result = scanner.scan(text)
        outcome = DlpOutcome(
            policy_name=self._policies[idx]["name"],
            matches=result.matches,
        )

        if result.has_block:
            outcome.action = "block"
            return outcome

        if result.has_reroute:
            outcome.action = "reroute"
            outcome.reroute_to = result.reroute_target
            return outcome

        if result.redacted_text is not None:
            outcome.action = "redact"
            outcome.redacted_text = result.redacted_text
            return outcome

        if result.matches:
            outcome.action = "log"

        return outcome

    def check_inbound(self, content: str, provider: str) -> DlpOutcome:
        """Scan inbound response content before returning to caller."""
        idx = self._select_policy_index(provider)
        if idx is None or self._inbound_scanners[idx] is None:
            return DlpOutcome()

        scanner = self._inbound_scanners[idx]  # type: ignore[assignment]
        result = scanner.scan(content)
        outcome = DlpOutcome(
            policy_name=self._policies[idx]["name"],
            matches=result.matches,
        )

        if result.has_block:
            outcome.action = "block"
            return outcome

        if result.redacted_text is not None:
            outcome.action = "redact"
            outcome.redacted_text = result.redacted_text
            return outcome

        if result.matches:
            outcome.action = "log"

        return outcome

    def scan_text(self, text: str, provider: str = "") -> list[DlpMatch]:
        """Scan text against all policies (admin/testing endpoint)."""
        all_matches: list[DlpMatch] = []
        for scanner in self._inbound_scanners + self._outbound_scanners:
            if scanner is None:
                continue
            result = scanner.scan(text)
            all_matches.extend(result.matches)
        return all_matches
