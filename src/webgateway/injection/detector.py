"""Composite prompt injection detector (PRD §27.3–§27.4).

Orchestrates all enabled detection layers, aggregates scores, and
determines the action to take (block/alert/scrub) based on config
thresholds and actions.
"""

from __future__ import annotations

import logging

from webgateway.config import PromptInjectionConfig
from webgateway.injection.classifier import OnnxClassifierLayer
from webgateway.injection.heuristics import HeuristicLayer
from webgateway.injection.judge import InjectionJudge
from webgateway.injection.types import InjectionDetectionResult, LayerName

logger = logging.getLogger(__name__)

SCRUB_REPLACEMENT = "[CONTENT REDACTED: PROMPT INJECTION DETECTED]"


class InjectionDetector:
    """Composite detector running all enabled layers.

    Constructed once at startup from config. ``detect()`` is called per
    request from the post-processing pipeline (Stage 5).
    """

    def __init__(self, config: PromptInjectionConfig) -> None:
        self._config = config
        self._enabled = config.enabled

        layers = config.layers

        # Layer 1: Heuristics (always available when enabled)
        self._heuristic: HeuristicLayer | None = None
        if layers.rebuff.enabled:
            self._heuristic = HeuristicLayer(
                custom_patterns=layers.rebuff.custom_patterns,
            )

        # Layer 2: ONNX classifier (graceful degradation)
        self._classifier: OnnxClassifierLayer | None = None
        if layers.onnx_classifier.enabled:
            self._classifier = OnnxClassifierLayer(
                model_path=layers.onnx_classifier.model_path,
            )

        # Layer 3: LLM judge escalation (opt-in)
        self._judge: InjectionJudge | None = None
        if layers.llm_judge.enabled:
            self._judge = InjectionJudge(
                enabled=True,
                base_url="http://127.0.0.1:1234/v1",
                model=layers.llm_judge.model,
                excerpt_max_chars=layers.llm_judge.excerpt_max_chars,
            )

    @property
    def enabled(self) -> bool:
        return self._enabled

    def detect(self, content: str, url: str) -> InjectionDetectionResult:
        """Run all enabled layers on *content*.

        Returns an :class:`InjectionDetectionResult` with the composite
        scores, recommended action, and scrubbed content (if action is scrub).
        """
        if not self._enabled:
            return InjectionDetectionResult(checked=False)

        if not content or not content.strip():
            return InjectionDetectionResult(checked=True)

        thresholds = self._config.thresholds

        # --- Layer 1: Heuristics ---
        heuristic_score = 0.0
        injection_type = None
        matched_patterns: list[str] = []
        layer_triggered: LayerName | None = None

        if self._heuristic is not None:
            h_result = self._heuristic.detect(content)
            heuristic_score = h_result.score
            if h_result.detected:
                injection_type = h_result.injection_type
                matched_patterns = h_result.matched_patterns
                layer_triggered = "rebuff"

        # --- Layer 2: ONNX Classifier ---
        classifier_score = 0.0
        if self._classifier is not None and self._classifier.is_available():
            c_result = self._classifier.score(content)
            classifier_score = c_result.score
            if classifier_score >= thresholds.classifier_score_alert:
                if injection_type is None:
                    injection_type = "instruction_override"
                if layer_triggered is None or classifier_score >= thresholds.classifier_score_block:
                    layer_triggered = "onnx_classifier"

        # --- Determine detection ---
        detected = (
            heuristic_score >= thresholds.heuristic_score_alert
            or classifier_score >= thresholds.classifier_score_alert
        )

        if not detected:
            return InjectionDetectionResult(
                checked=True,
                detected=False,
                heuristic_score=heuristic_score,
                classifier_score=classifier_score,
                action="none",
            )

        # --- Determine action ---
        action = self._determine_action(
            heuristic_score=heuristic_score,
            classifier_score=classifier_score,
            matched_patterns=matched_patterns,
        )

        # --- Scrub content if needed ---
        scrubbed_content = None
        scrubbed_segments = 0
        if action == "scrub":
            scrubbed_content, scrubbed_segments = self._scrub_content(
                content, matched_patterns
            )

        return InjectionDetectionResult(
            checked=True,
            detected=True,
            injection_type=injection_type,
            layer_triggered=layer_triggered,
            heuristic_score=round(heuristic_score, 4),
            classifier_score=round(classifier_score, 4),
            action=action,
            scrubbed_content=scrubbed_content,
            scrubbed_segments=scrubbed_segments,
            matched_patterns=matched_patterns if matched_patterns else None,
        )

    def _determine_action(
        self,
        heuristic_score: float,
        classifier_score: float,
        matched_patterns: list[str],
    ) -> str:
        """Determine the action based on scores, thresholds, and config."""
        thresholds = self._config.thresholds
        actions = self._config.actions

        # Block threshold — highest priority
        if (
            heuristic_score >= thresholds.heuristic_score_block
            or classifier_score >= thresholds.classifier_score_block
        ):
            return (
                actions.on_high_score
                if actions.on_high_score == "block"
                else actions.on_pattern_match
            )

        # Pattern match (heuristic detected something)
        if matched_patterns:
            return actions.on_pattern_match

        # High classifier score but no heuristic match
        if classifier_score >= thresholds.classifier_score_alert:
            return actions.on_high_score

        return "none"

    def _scrub_content(
        self,
        content: str,
        matched_patterns: list[str],
    ) -> tuple[str, int]:
        """Redact detected injection text from content.

        Uses the heuristic layer's patterns to find and replace injection
        text with a placeholder.
        """
        if not self._heuristic:
            return content, 0

        scrubbed = content
        segments = 0

        for pattern, _, _label in self._heuristic._patterns:
            new_scrubbed, count = pattern.subn(SCRUB_REPLACEMENT, scrubbed)
            if count > 0:
                scrubbed = new_scrubbed
                segments += count

        return scrubbed, segments
