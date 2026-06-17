"""Layer 1: Rebuff-inspired heuristic pattern matching.

Uses built-in regex patterns covering the most common prompt injection
signatures. Falls back to built-in patterns if the ``rebuff`` library
is not installed.

Pattern categories (PRD §27.3 Layer 1):
- Instruction override variants
- Role hijack attempts
- Exfiltration attempts
- Fake role tags
- LLM control tokens
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass, field

from webgateway.injection.types import InjectionType

# Each pattern maps to an injection type. Ordered by priority.
_DEFAULT_PATTERNS: list[tuple[str, InjectionType, str]] = [
    # Instruction override
    (
        r"(?i)ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?",
        "instruction_override",
        "instruction_override",
    ),
    (
        r"(?i)disregard\s+(?:all\s+)?(?:prior|previous|above)\s+(?:directives?|instructions?)",
        "instruction_override",
        "instruction_override",
    ),
    (
        r"(?i)forget\s+(?:everything|all\s+(?:previous|prior))",
        "instruction_override",
        "instruction_override",
    ),
    (r"(?i)disregard\s+(?:the\s+)?above", "instruction_override", "instruction_override"),

    # Role hijack
    (r"(?i)you\s+are\s+now\s+(?:a|an)\b", "role_hijack", "role_hijack"),
    (r"(?i)act\s+as\s+(?:if\s+you\s+are\s+)?(?:a|an)\b", "role_hijack", "role_hijack"),
    (r"(?i)pretend\s+(?:you\s+are|to\s+be)\b", "role_hijack", "role_hijack"),
    (r"(?i)from\s+now\s+on[,\s]+you\s+are\b", "role_hijack", "role_hijack"),

    # Exfiltration
    (r"(?i)repeat\s+(?:your\s+)?system\s+prompt", "exfiltration_attempt", "exfiltration"),
    (
        r"(?i)(?:send|post|exfiltrate|transmit)\s+(?:the\s+)?"
        r"(?:conversation|chat\s+history|context)\b",
        "exfiltration_attempt",
        "exfiltration",
    ),
    (
        r"(?i)(?:print|reveal|show|output)\s+(?:your\s+)?"
        r"(?:instructions?|rules?|system\s+message)\b",
        "exfiltration_attempt",
        "exfiltration",
    ),

    # Action hijack
    (
        r"(?i)(?:execute|run|navigate\s+to|visit|open)\s+(?:the\s+)?"
        r"(?:url|link|website|endpoint)\b",
        "action_hijack",
        "action_hijack",
    ),
    (
        r"(?i)(?:call|invoke|trigger)\s+(?:the\s+)?(?:api|function|tool|endpoint)\b",
        "action_hijack",
        "action_hijack",
    ),

    # Fake role tags
    (r"\[(?:SYSTEM|INST|USER|ASSISTANT)\]", "fake_role_tag", "fake_role_tag"),
    (r"<\|im_start\|>", "fake_role_tag", "fake_role_tag"),
    (r"</?(?:system|developer|tool)>", "fake_role_tag", "fake_role_tag"),

    # LLM control tokens
    (r"<\|endoftext\|>", "llm_control_token", "llm_control_token"),
    (r"<\|im_end\|>", "llm_control_token", "llm_control_token"),
    (r"<\|start_header_id\|>", "llm_control_token", "llm_control_token"),
    (r"<\|end_header_id\|>", "llm_control_token", "llm_control_token"),
]


@dataclass
class HeuristicResult:
    """Result of the heuristic detection layer."""

    detected: bool = False
    score: float = 0.0
    injection_type: InjectionType | None = None
    matched_patterns: list[str] = field(default_factory=list)


class HeuristicLayer:
    """Regex-based prompt injection pattern matcher.

    Wraps the built-in pattern library. Custom patterns from config are
    additive — they extend, not replace, the default library.
    """

    def __init__(self, custom_patterns: list[str] | None = None) -> None:
        self._patterns: list[tuple[re.Pattern, InjectionType, str]] = [
            (re.compile(pat), itype, label)
            for pat, itype, label in _DEFAULT_PATTERNS
        ]
        # Custom patterns default to "instruction_override" type since they're
        # operator-defined and most commonly target override attempts.
        for custom in custom_patterns or []:
            with contextlib.suppress(re.error):
                self._patterns.append(
                    (re.compile(custom), "instruction_override", custom)
                )

    def detect(self, content: str) -> HeuristicResult:
        """Run all patterns against *content*.

        Returns the highest-priority match type and a score proportional
        to the number of unique pattern categories triggered.
        """
        if not content:
            return HeuristicResult()

        matched_labels: list[str] = []
        triggered_types: set[InjectionType] = set()

        for pattern, itype, label in self._patterns:
            if pattern.search(content):
                matched_labels.append(label)
                triggered_types.add(itype)

        if not matched_labels:
            return HeuristicResult()

        # Score: number of distinct injection categories detected, capped at 1.0.
        # Each category contributes 0.3, so 4+ categories = max score.
        score = min(1.0, len(triggered_types) * 0.3)

        # Determine primary injection type by first-match priority
        # (patterns are ordered by priority in _DEFAULT_PATTERNS)
        primary_type: InjectionType | None = None
        for _, itype, _ in self._patterns:
            if itype in triggered_types:
                primary_type = itype
                break

        return HeuristicResult(
            detected=True,
            score=score,
            injection_type=primary_type,
            matched_patterns=matched_labels,
        )
