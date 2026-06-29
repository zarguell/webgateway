"""Prompt injection detection (PRD §27).

Standard tier (v1): Rebuff heuristics + MiniLM ONNX classifier.
Optional: LLM judge escalation, Lakera Guard.
"""

from serp_llm.injection.detector import InjectionDetector
from serp_llm.injection.events import EventLogger
from serp_llm.injection.types import (
    InjectionBlockedError,
    InjectionDetectionResult,
)

__all__ = [
    "InjectionBlockedError",
    "InjectionDetectionResult",
    "InjectionDetector",
    "EventLogger",
]
