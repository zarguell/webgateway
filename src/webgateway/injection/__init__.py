"""Prompt injection detection (PRD §27).

Standard tier (v1): Rebuff heuristics + MiniLM ONNX classifier.
Optional: LLM judge escalation, Lakera Guard.
"""

from webgateway.injection.detector import InjectionDetector
from webgateway.injection.events import EventLogger
from webgateway.injection.types import (
    InjectionBlockedError,
    InjectionDetectionResult,
)

__all__ = [
    "InjectionBlockedError",
    "InjectionDetectionResult",
    "InjectionDetector",
    "EventLogger",
]
