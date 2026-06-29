"""Tests for the refactored LLMJudgeConfig."""
from __future__ import annotations

from serp_llm.config import GatewayConfig, LLMJudgeConfig


class TestLLMJudgeConfig:
    def test_defaults(self):
        config = LLMJudgeConfig()
        assert config.enabled is False
        assert config.model == "google/gemma-4-e2b"
        assert config.base_url == "http://127.0.0.1:1234/v1"
        assert config.api_key == "lm-studio"
        assert config.trigger_on_policy_miss is True
        assert config.trigger_on_retry is True
        assert config.trigger_on_error_class == ["403", "429", "bot_detected", "timeout"]
        assert config.cache_decisions is True
        assert config.cache_ttl_seconds == 3600
        assert config.confidence_threshold == 0.70
        assert config.timeout == 180
        assert config.temperature == 0.0

    def test_no_ollama_fields(self):
        """Ollama-specific fields must not exist."""
        config = LLMJudgeConfig()
        assert not hasattr(config, "ollama_url")
        assert not hasattr(config, "triggers")

    def test_parses_from_dict(self):
        """Config should parse from a dict matching the YAML structure."""
        raw = {
            "enabled": True,
            "model": "qwen2.5:3b",
            "base_url": "http://localhost:1234/v1",
            "api_key": "my-key",
            "trigger_on_policy_miss": False,
            "trigger_on_retry": False,
            "trigger_on_error_class": ["500"],
            "confidence_threshold": 0.85,
        }
        config = LLMJudgeConfig.model_validate(raw)
        assert config.enabled is True
        assert config.model == "qwen2.5:3b"
        assert config.trigger_on_policy_miss is False
        assert config.confidence_threshold == 0.85

    def test_gateway_config_includes_judge(self):
        """GatewayConfig should include llm_judge with new defaults."""
        raw = {
            "defaults": {
                "search_provider": "searxng",
                "extract_provider": "jina",
                "timeout": 15,
                "retry": {"strategy": "fallback", "max_attempts": 3, "fallback_chain": []},
            },
            "providers": {},
            "policies": [],
            "auth": {"keys": []},
        }
        config = GatewayConfig.model_validate(raw)
        assert config.llm_judge.enabled is False
        assert config.llm_judge.base_url == "http://127.0.0.1:1234/v1"
