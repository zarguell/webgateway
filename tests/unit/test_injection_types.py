from __future__ import annotations

import webgateway.config as cfg


class TestPromptInjectionConfig:
    def test_defaults_all_disabled(self):
        """When no config provided, prompt injection is disabled by default."""
        pi = cfg.PromptInjectionConfig()
        assert pi.enabled is False
        assert pi.layers.rebuff.enabled is True
        assert pi.layers.onnx_classifier.enabled is True
        assert pi.layers.llm_judge.enabled is False
        assert pi.layers.lakera_guard.enabled is False

    def test_thresholds_defaults(self):
        pi = cfg.PromptInjectionConfig()
        assert pi.thresholds.heuristic_score_alert == 0.5
        assert pi.thresholds.heuristic_score_block == 0.85
        assert pi.thresholds.classifier_score_alert == 0.6
        assert pi.thresholds.classifier_score_block == 0.90
        assert pi.thresholds.llm_judge_escalate == 0.65

    def test_actions_defaults(self):
        pi = cfg.PromptInjectionConfig()
        assert pi.actions.on_pattern_match == "scrub"
        assert pi.actions.on_high_score == "alert"
        assert pi.actions.on_judge_confirmed == "block"
        assert pi.actions.on_lakera_detected == "block"

    def test_exemptions_defaults(self):
        pi = cfg.PromptInjectionConfig()
        assert pi.exemptions.domains == []
        assert pi.exemptions.api_key_ids == []

    def test_full_config_from_dict(self):
        """Validate a complete prompt_injection config section parses correctly."""
        raw = {
            "enabled": True,
            "layers": {
                "rebuff": {
                    "enabled": True,
                    "custom_patterns": ["(?i)test pattern"],
                    "vector_db": {
                        "enabled": False,
                        "provider": "chroma_sqlite",
                        "path": "/app/data/iv",
                    },
                    "embeddings": {
                        "provider": "ollama",
                        "model": "nomic-embed-text",
                        "url": "http://ollama:11434",
                    },
                },
                "onnx_classifier": {
                    "enabled": True,
                    "model_path": "/app/models/defender-minilm.onnx",
                    "threshold": 0.85,
                },
                "llm_judge": {
                    "enabled": False,
                    "model": "ollama/gemma3:1b",
                    "excerpt_max_chars": 500,
                },
                "lakera_guard": {
                    "enabled": False,
                    "api_key": "${LAKERA_API_KEY}",
                    "dlp_acknowledgement": False,
                },
            },
            "thresholds": {
                "heuristic_score_alert": 0.5,
                "heuristic_score_block": 0.85,
                "classifier_score_alert": 0.6,
                "classifier_score_block": 0.90,
                "llm_judge_escalate": 0.65,
            },
            "actions": {
                "on_pattern_match": "scrub",
                "on_high_score": "alert",
                "on_judge_confirmed": "block",
                "on_lakera_detected": "block",
            },
            "exemptions": {
                "domains": ["docs.python.org", "developer.mozilla.org"],
                "api_key_ids": ["key_trusted"],
            },
        }
        pi = cfg.PromptInjectionConfig.model_validate(raw)
        assert pi.enabled is True
        assert pi.layers.rebuff.custom_patterns == ["(?i)test pattern"]
        assert pi.layers.onnx_classifier.threshold == 0.85
        assert pi.exemptions.domains == ["docs.python.org", "developer.mozilla.org"]

    def test_gateway_config_has_prompt_injection(self):
        """GatewayConfig should include prompt_injection with safe defaults."""
        gc = cfg.GatewayConfig()
        assert hasattr(gc, "prompt_injection")
        assert gc.prompt_injection.enabled is False


class TestInjectionTypes:
    def test_injection_blocked_error_carries_metadata(self):
        from webgateway.injection.types import InjectionBlockedError

        err = InjectionBlockedError(
            url="https://evil.com",
            injection_type="instruction_override",
            layer_triggered="onnx_classifier",
            heuristic_score=0.62,
            classifier_score=0.91,
        )
        assert err.url == "https://evil.com"
        assert err.injection_type == "instruction_override"
        assert err.layer_triggered == "onnx_classifier"
        assert err.classifier_score == 0.91
        assert "prompt injection" in str(err).lower()

    def test_injection_detection_result_defaults(self):
        from webgateway.injection.types import InjectionDetectionResult

        result = InjectionDetectionResult()
        assert result.checked is False
        assert result.detected is False
        assert result.injection_type is None
        assert result.layer_triggered is None
        assert result.heuristic_score == 0.0
        assert result.classifier_score == 0.0
        assert result.action == "none"
        assert result.scrubbed_content is None
        assert result.scrubbed_segments == 0

    def test_injection_detection_result_detected(self):
        from webgateway.injection.types import InjectionDetectionResult

        result = InjectionDetectionResult(
            checked=True,
            detected=True,
            injection_type="role_hijack",
            layer_triggered="rebuff",
            heuristic_score=0.9,
            classifier_score=0.3,
            action="scrub",
            scrubbed_content="clean text",
            scrubbed_segments=1,
        )
        assert result.detected is True
        assert result.action == "scrub"
        assert result.scrubbed_segments == 1


class TestInjectionSchemas:
    def test_prompt_injection_override_defaults(self):
        from webgateway.schemas import PromptInjectionOverride
        override = PromptInjectionOverride()
        assert override.skip is False

    def test_prompt_injection_info_defaults(self):
        from webgateway.schemas import PromptInjectionInfo
        info = PromptInjectionInfo()
        assert info.checked is False
        assert info.detected is False
        assert info.injection_type is None
        assert info.action_taken == "none"

    def test_extract_request_accepts_prompt_injection_override(self):
        from webgateway.schemas import ExtractRequest, PromptInjectionOverride
        req = ExtractRequest(
            url="https://example.com",
            prompt_injection=PromptInjectionOverride(skip=True),
        )
        assert req.prompt_injection is not None
        assert req.prompt_injection.skip is True

    def test_extract_request_prompt_injection_optional(self):
        from webgateway.schemas import ExtractRequest
        req = ExtractRequest(url="https://example.com")
        assert req.prompt_injection is None

    def test_extract_response_accepts_prompt_injection_info(self):
        from webgateway.schemas import ExtractResponse, PromptInjectionInfo
        resp = ExtractResponse(
            content="text",
            url="https://example.com",
            provider_used="jina",
            request_id="req_abc",
            latency_ms=100,
            prompt_injection=PromptInjectionInfo(detected=True),
        )
        assert resp.prompt_injection is not None
        assert resp.prompt_injection.detected is True
