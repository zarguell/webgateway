"""Core data structures for prompt injection detection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# PRD §27.7 — Injection type taxonomy
InjectionType = Literal[
    "instruction_override",
    "role_hijack",
    "exfiltration_attempt",
    "action_hijack",
    "fake_role_tag",
    "hidden_text",
    "llm_control_token",
]

LayerName = Literal["rebuff", "onnx_classifier", "llm_judge", "lakera_guard"]


@dataclass
class InjectionDetectionResult:
    """Outcome of running all enabled detection layers on a piece of content.

    The ``action`` field is the *recommended* action based on config thresholds.
    The caller (service layer) is responsible for executing block/alert/scrub.
    """

    checked: bool = False
    detected: bool = False
    injection_type: InjectionType | None = None
    layer_triggered: LayerName | None = None
    heuristic_score: float = 0.0
    classifier_score: float = 0.0
    judge_confirmed: bool = False
    action: Literal["none", "block", "alert", "scrub"] = "none"
    scrubbed_content: str | None = None
    scrubbed_segments: int = 0
    matched_patterns: list[str] | None = None


class InjectionBlockedError(Exception):
    """Raised when prompt injection detection blocks a response.

    The service layer constructs the structured error response (PRD §27.4)
    from this exception's fields.
    """

    def __init__(
        self,
        url: str,
        injection_type: InjectionType | None,
        layer_triggered: LayerName | None,
        heuristic_score: float = 0.0,
        classifier_score: float = 0.0,
    ):
        self.url = url
        self.injection_type = injection_type
        self.layer_triggered = layer_triggered
        self.heuristic_score = heuristic_score
        self.classifier_score = classifier_score
        super().__init__(
            f"Content blocked: prompt injection detected "
            f"(type={injection_type}, layer={layer_triggered})"
        )
