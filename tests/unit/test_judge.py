"""Unit tests for the LLM Judge module."""
from __future__ import annotations

import json
import time
from unittest.mock import MagicMock

from pytest_httpx import HTTPXMock

from webgateway.config import ConfigManager, GatewayConfig
from webgateway.judge import (
    DecisionCache,
    FailedProvider,
    JudgeContext,
    JudgeResponse,
    LLMJudge,
    ProviderInfo,
    extract_judge_json,
)
from webgateway.providers.base import ProviderMetadata
from webgateway.providers.registry import ProviderRegistry


class TestDecisionCache:
    def test_miss_returns_none(self):
        cache = DecisionCache(ttl_seconds=60)
        assert cache.get("nonexistent") is None

    def test_set_then_get(self):
        cache = DecisionCache(ttl_seconds=60)
        from webgateway.policy.models import RoutingDecision

        decision = RoutingDecision(
            policy_matched="llm_judge",
            content_type="search",
            provider="searxng",
            judge_invoked=True,
            judge_reasoning_tag="test",
        )
        cache.set("key1", decision)
        assert cache.get("key1") is decision

    def test_ttl_expiry(self):
        cache = DecisionCache(ttl_seconds=0)
        from webgateway.policy.models import RoutingDecision

        decision = RoutingDecision(
            policy_matched="llm_judge",
            content_type="search",
            provider="searxng",
        )
        cache.set("key1", decision)
        time.sleep(0.01)
        assert cache.get("key1") is None

    def test_clear(self):
        cache = DecisionCache(ttl_seconds=60)
        from webgateway.policy.models import RoutingDecision

        decision = RoutingDecision(
            policy_matched="llm_judge",
            content_type="search",
            provider="searxng",
        )
        cache.set("key1", decision)
        cache.clear()
        assert cache.get("key1") is None


class TestDataclasses:
    def test_failed_provider(self):
        fp = FailedProvider(name="jina", error_class="403", message="Forbidden")
        assert fp.name == "jina"
        assert fp.error_class == "403"
        assert fp.message == "Forbidden"

    def test_provider_info(self):
        pi = ProviderInfo(name="searxng", specialization="general", healthy=True, self_hosted=True)
        assert pi.name == "searxng"
        assert pi.specialization == "general"

    def test_judge_context(self):
        ctx = JudgeContext(
            trigger_type="on_policy_miss",
            content_type="search",
            url=None,
            query="python async",
            failed_providers=[],
            available_providers=[],
        )
        assert ctx.trigger_type == "on_policy_miss"
        assert ctx.query == "python async"

    def test_judge_response(self):
        resp = JudgeResponse(
            provider="firecrawl",
            fallback_if_fail="crawl4ai",
            reasoning_tag="js_heavy_page",
            confidence=0.85,
        )
        assert resp.provider == "firecrawl"
        assert resp.confidence == 0.85


class TestExtractJudgeJson:
    def test_clean_json(self):
        text = (
            '{"provider": "searxng", "fallback_if_fail": null, '
            '"reasoning_tag": "general", "confidence": 0.9}'
        )
        result = extract_judge_json(text)
        assert result is not None
        assert result.provider == "searxng"
        assert result.confidence == 0.9

    def test_json_in_markdown_fences(self):
        text = (
            '```json\n{"provider": "jina", "fallback_if_fail": "firecrawl", '
            '"reasoning_tag": "test", "confidence": 0.7}\n```'
        )
        result = extract_judge_json(text)
        assert result is not None
        assert result.provider == "jina"

    def test_json_with_surrounding_prose(self):
        text = (
            'Here is my decision:\n'
            '{"provider": "brave", "fallback_if_fail": null, '
            '"reasoning_tag": "x", "confidence": 0.5}\n'
            'That is all.'
        )
        result = extract_judge_json(text)
        assert result is not None
        assert result.provider == "brave"

    def test_missing_required_key(self):
        text = '{"provider": "searxng", "confidence": 0.9}'
        result = extract_judge_json(text)
        assert result is None

    def test_malformed_json(self):
        text = "this is not json at all"
        result = extract_judge_json(text)
        assert result is None

    def test_empty_string(self):
        result = extract_judge_json("")
        assert result is None

    def test_confidence_as_string(self):
        text = (
            '{"provider": "searxng", "fallback_if_fail": null, '
            '"reasoning_tag": "x", "confidence": "0.85"}'
        )
        result = extract_judge_json(text)
        assert result is not None
        assert result.confidence == 0.85


# ---------------------------------------------------------------------------
# LLMJudge tests
# ---------------------------------------------------------------------------


def _make_config_manager(enabled: bool = True, **overrides) -> MagicMock:
    """Build a ConfigManager mock with an LLMJudgeConfig."""
    config_data = {
        "enabled": enabled,
        "model": "google/gemma-4-e2b",
        "base_url": "http://127.0.0.1:1234/v1",
        "api_key": "lm-studio",
        "trigger_on_policy_miss": True,
        "trigger_on_retry": True,
        "trigger_on_error_class": ["403", "429", "bot_detected", "timeout"],
        "cache_decisions": True,
        "cache_ttl_seconds": 3600,
        "confidence_threshold": 0.70,
        "timeout": 10,
        "temperature": 0.0,
    }
    config_data.update(overrides)

    cm = MagicMock(spec=ConfigManager)
    config = MagicMock(spec=GatewayConfig)
    # Build a simple namespace object for llm_judge config
    class _NS:
        pass
    ns = _NS()
    for k, v in config_data.items():
        setattr(ns, k, v)
    config.llm_judge = ns
    # Mock providers dict
    config.providers = {}
    cm.config = config
    return cm


def _make_registry_mock(names: list[str] | None = None) -> MagicMock:
    """Build a ProviderRegistry mock."""
    reg = MagicMock(spec=ProviderRegistry)
    all_names = names or ["searxng", "jina", "firecrawl"]
    reg.list_names.return_value = all_names
    reg.has.return_value = True

    metas = []
    for name in all_names:
        meta = MagicMock(spec=ProviderMetadata)
        meta.name = name
        meta.self_hosted = name == "searxng"
        metas.append(meta)
    reg.list_metadata.return_value = metas
    return reg


class TestShouldTriggerForError:
    def test_trigger_on_retry_true(self):
        cm = _make_config_manager(trigger_on_retry=True)
        reg = _make_registry_mock()
        judge = LLMJudge(cm, reg)
        assert judge.should_trigger_for_error("500") is True

    def test_trigger_on_retry_false_but_error_class_matches(self):
        cm = _make_config_manager(
            trigger_on_retry=False,
            trigger_on_error_class=["403", "429"],
        )
        reg = _make_registry_mock()
        judge = LLMJudge(cm, reg)
        assert judge.should_trigger_for_error("403") is True
        assert judge.should_trigger_for_error("429") is True

    def test_no_trigger(self):
        cm = _make_config_manager(
            trigger_on_retry=False,
            trigger_on_error_class=[],
        )
        reg = _make_registry_mock()
        judge = LLMJudge(cm, reg)
        assert judge.should_trigger_for_error("500") is False


class TestEvaluatePolicyMiss:
    async def test_returns_decision_on_valid_response(
        self, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="POST",
            url="http://127.0.0.1:1234/v1/chat/completions",
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps({
                                "provider": "firecrawl",
                                "fallback_if_fail": "jina",
                                "reasoning_tag": "js_heavy_page",
                                "confidence": 0.85,
                            })
                        }
                    }
                ]
            },
        )
        cm = _make_config_manager()
        reg = _make_registry_mock(["searxng", "jina", "firecrawl"])
        judge = LLMJudge(cm, reg)

        decision = await judge.evaluate_policy_miss(
            content_type="extract",
            url="https://example.com/app",
            query=None,
        )
        assert decision is not None
        assert decision.provider == "firecrawl"
        assert decision.fallback_chain == ["firecrawl", "jina"]
        assert decision.judge_invoked is True
        assert decision.judge_reasoning_tag == "js_heavy_page"
        assert decision.policy_matched == "llm_judge"

    async def test_returns_none_when_disabled(self):
        cm = _make_config_manager(enabled=False)
        reg = _make_registry_mock()
        judge = LLMJudge(cm, reg)
        result = await judge.evaluate_policy_miss("search", None, "test")
        assert result is None

    async def test_returns_none_on_low_confidence(
        self, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="POST",
            url="http://127.0.0.1:1234/v1/chat/completions",
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps({
                                "provider": "searxng",
                                "fallback_if_fail": None,
                                "reasoning_tag": "low",
                                "confidence": 0.50,
                            })
                        }
                    }
                ]
            },
        )
        cm = _make_config_manager(confidence_threshold=0.70)
        reg = _make_registry_mock()
        judge = LLMJudge(cm, reg)
        result = await judge.evaluate_policy_miss("search", None, "test")
        assert result is None

    async def test_returns_none_on_llm_error(
        self, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="POST",
            url="http://127.0.0.1:1234/v1/chat/completions",
            status_code=500,
        )
        cm = _make_config_manager()
        reg = _make_registry_mock()
        judge = LLMJudge(cm, reg)
        result = await judge.evaluate_policy_miss("search", None, "test")
        assert result is None

    async def test_returns_none_on_bad_json(
        self, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="POST",
            url="http://127.0.0.1:1234/v1/chat/completions",
            json={"choices": [{"message": {"content": "not json"}}]},
        )
        cm = _make_config_manager()
        reg = _make_registry_mock()
        judge = LLMJudge(cm, reg)
        result = await judge.evaluate_policy_miss("search", None, "test")
        assert result is None

    async def test_returns_none_on_unknown_provider(
        self, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="POST",
            url="http://127.0.0.1:1234/v1/chat/completions",
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps({
                                "provider": "nonexistent",
                                "fallback_if_fail": None,
                                "reasoning_tag": "x",
                                "confidence": 0.9,
                            })
                        }
                    }
                ]
            },
        )
        cm = _make_config_manager()
        reg = _make_registry_mock(["searxng"])
        judge = LLMJudge(cm, reg)
        result = await judge.evaluate_policy_miss("search", None, "test")
        assert result is None

    async def test_cache_hit_skips_llm_call(
        self, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="POST",
            url="http://127.0.0.1:1234/v1/chat/completions",
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps({
                                "provider": "searxng",
                                "fallback_if_fail": None,
                                "reasoning_tag": "cached",
                                "confidence": 0.9,
                            })
                        }
                    }
                ]
            },
        )
        cm = _make_config_manager()
        reg = _make_registry_mock(["searxng"])
        judge = LLMJudge(cm, reg)

        d1 = await judge.evaluate_policy_miss("search", None, "test query")
        assert d1 is not None
        assert d1.judge_reasoning_tag == "cached"

        d2 = await judge.evaluate_policy_miss("search", None, "test query")
        assert d2 is not None
        assert d2.judge_reasoning_tag == "cached"

        assert len(httpx_mock.get_requests()) == 1


class TestEvaluateForRetry:
    async def test_returns_decision_on_retry(
        self, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="POST",
            url="http://127.0.0.1:1234/v1/chat/completions",
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps({
                                "provider": "firecrawl",
                                "fallback_if_fail": "searxng",
                                "reasoning_tag": "anti_bot_retry",
                                "confidence": 0.80,
                            })
                        }
                    }
                ]
            },
        )
        cm = _make_config_manager()
        reg = _make_registry_mock(["searxng", "jina", "firecrawl"])
        judge = LLMJudge(cm, reg)

        decision = await judge.evaluate_for_retry(
            content_type="extract",
            url="https://example.com",
            query=None,
            failed_providers=[
                FailedProvider(name="jina", error_class="403", message="Forbidden"),
            ],
        )
        assert decision is not None
        assert decision.provider == "firecrawl"
        assert decision.judge_reasoning_tag == "anti_bot_retry"

    async def test_returns_none_when_retry_not_triggered(self):
        cm = _make_config_manager(
            trigger_on_retry=False,
            trigger_on_error_class=["429"],
        )
        reg = _make_registry_mock()
        judge = LLMJudge(cm, reg)
        result = await judge.evaluate_for_retry(
            "search", None, "test",
            [FailedProvider(name="jina", error_class="403", message="x")],
        )
        assert result is None

    async def test_rejects_already_failed_provider(
        self, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="POST",
            url="http://127.0.0.1:1234/v1/chat/completions",
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps({
                                "provider": "jina",
                                "fallback_if_fail": None,
                                "reasoning_tag": "x",
                                "confidence": 0.9,
                            })
                        }
                    }
                ]
            },
        )
        cm = _make_config_manager()
        reg = _make_registry_mock(["jina", "searxng"])
        judge = LLMJudge(cm, reg)
        result = await judge.evaluate_for_retry(
            "extract", "https://example.com", None,
            [FailedProvider(name="jina", error_class="403", message="Forbidden")],
        )
        assert result is None


# ---------------------------------------------------------------------------
# GatewayService <-> LLMJudge integration
# ---------------------------------------------------------------------------


from webgateway.judge import LLMJudge as LLMJudgeClass  # noqa: E402
from webgateway.policy.models import RoutingDecision  # noqa: E402
from webgateway.service import GatewayService  # noqa: E402


def _make_judge(enabled: bool = True) -> MagicMock:
    """Build a mock LLMJudge."""
    judge = MagicMock(spec=LLMJudgeClass)
    judge.is_enabled.return_value = enabled
    judge.evaluate_policy_miss.return_value = None
    judge.evaluate_for_retry.return_value = None
    judge.should_trigger_for_error.return_value = False
    return judge


class TestServicePolicyMissIntegration:
    async def test_judge_called_on_policy_miss(self):
        """When policy_matched is None and judge is enabled, evaluate_policy_miss fires."""
        cm = MagicMock()
        cm.config.defaults.timeout = 15
        cm.config.defaults.search_provider = "searxng"
        cm.config.defaults.extract_provider = "jina"

        engine = MagicMock()
        miss_decision = RoutingDecision(
            policy_matched=None,
            content_type="search",
            provider="searxng",
        )
        engine.evaluate.return_value = miss_decision

        judge = _make_judge(enabled=True)
        judge_decision = RoutingDecision(
            policy_matched="llm_judge",
            content_type="search",
            provider="brave",
            judge_invoked=True,
            judge_reasoning_tag="semantic_query",
        )
        judge.evaluate_policy_miss.return_value = judge_decision

        service = GatewayService(
            cm, engine, MagicMock(), MagicMock(), MagicMock(),
            llm_judge=judge,
        )

        result = await service._maybe_judge_policy_miss(
            miss_decision, content_type="search", url=None, query="test"
        )
        assert result is judge_decision
        judge.evaluate_policy_miss.assert_called_once_with(
            content_type="search", url=None, query="test"
        )

    async def test_judge_not_called_when_policy_matches(self):
        """When policy_matched is not None, judge should NOT fire."""
        cm = MagicMock()
        engine = MagicMock()
        matched_decision = RoutingDecision(
            policy_matched="some_rule",
            content_type="search",
            provider="searxng",
        )

        judge = _make_judge(enabled=True)

        service = GatewayService(
            cm, engine, MagicMock(), MagicMock(), MagicMock(),
            llm_judge=judge,
        )

        result = await service._maybe_judge_policy_miss(
            matched_decision, content_type="search", url=None, query="test"
        )
        assert result is matched_decision
        judge.evaluate_policy_miss.assert_not_called()

    async def test_judge_not_called_when_disabled(self):
        cm = MagicMock()
        engine = MagicMock()
        miss_decision = RoutingDecision(
            policy_matched=None,
            content_type="search",
            provider="searxng",
        )

        judge = _make_judge(enabled=False)

        service = GatewayService(
            cm, engine, MagicMock(), MagicMock(), MagicMock(),
            llm_judge=judge,
        )

        result = await service._maybe_judge_policy_miss(
            miss_decision, content_type="search", url=None, query="test"
        )
        assert result is miss_decision
        judge.evaluate_policy_miss.assert_not_called()

    async def test_judge_returns_none_falls_through(self):
        """When judge returns None, the original decision is kept."""
        cm = MagicMock()
        engine = MagicMock()
        miss_decision = RoutingDecision(
            policy_matched=None,
            content_type="search",
            provider="searxng",
        )

        judge = _make_judge(enabled=True)
        judge.evaluate_policy_miss.return_value = None

        service = GatewayService(
            cm, engine, MagicMock(), MagicMock(), MagicMock(),
            llm_judge=judge,
        )

        result = await service._maybe_judge_policy_miss(
            miss_decision, content_type="search", url=None, query="test"
        )
        assert result is miss_decision

    async def test_no_judge_provided(self):
        """Service works fine without a judge."""
        cm = MagicMock()
        engine = MagicMock()
        miss_decision = RoutingDecision(
            policy_matched=None,
            content_type="search",
            provider="searxng",
        )

        service = GatewayService(
            cm, engine, MagicMock(), MagicMock(), MagicMock(),
        )

        result = await service._maybe_judge_policy_miss(
            miss_decision, content_type="search", url=None, query="test"
        )
        assert result is miss_decision


from unittest.mock import AsyncMock  # noqa: E402

from webgateway.providers.base import ProviderError  # noqa: E402


class TestServiceRetryIntegration:
    async def test_judge_called_on_provider_error(self):
        """When a provider fails with a trigger-matching error, judge fires."""
        cm = MagicMock()
        cm.config.defaults.timeout = 15

        engine = MagicMock()
        engine.evaluate_for_error.return_value = None  # No Tier 1 error rule

        registry = MagicMock()
        # First provider fails, judge suggests firecrawl which succeeds
        fail_provider = MagicMock()
        fail_provider.extract = MagicMock(
            side_effect=ProviderError(
                "jina", "Forbidden", status_code=403, error_class="403"
            )
        )
        firecrawl_provider = MagicMock()
        firecrawl_provider.extract = AsyncMock(
            return_value=MagicMock(content="# Success")
        )
        registry.get = MagicMock(
            side_effect=[fail_provider, firecrawl_provider]
        )

        judge = _make_judge(enabled=True)
        judge.should_trigger_for_error.return_value = True
        judge_decision = RoutingDecision(
            policy_matched="llm_judge",
            content_type="extract",
            provider="firecrawl",
            judge_invoked=True,
            judge_reasoning_tag="anti_bot_retry",
        )
        judge.evaluate_for_retry.return_value = judge_decision

        service = GatewayService(
            cm, engine, registry, MagicMock(), MagicMock(),
            llm_judge=judge,
        )

        async def operation(provider, opts):
            return await provider.extract(opts)

        decision = RoutingDecision(
            policy_matched=None,
            content_type="extract",
            provider="jina",
        )

        result, provider_used, quality = await service._execute_with_fallback(
            "jina",
            ["searxng"],
            operation,
            MagicMock(),
            content_type="extract",
            url="https://example.com",
            query=None,
            decision=decision,
        )

        # Judge should have been called
        judge.evaluate_for_retry.assert_called_once()
        assert decision.judge_invoked is True
        assert decision.judge_reasoning_tag == "anti_bot_retry"

    async def test_judge_not_called_on_non_triggering_error(self):
        """When error class doesn't match triggers, judge is not called."""
        cm = MagicMock()
        engine = MagicMock()
        engine.evaluate_for_error.return_value = None

        registry = MagicMock()
        fail_provider = MagicMock()
        fail_provider.search = MagicMock(
            side_effect=ProviderError(
                "jina", "Server Error", status_code=500, error_class="500"
            )
        )
        success_provider = MagicMock()
        success_provider.search = AsyncMock(
            return_value=MagicMock(results=[])
        )
        registry.get = MagicMock(
            side_effect=[fail_provider, success_provider]
        )

        judge = _make_judge(enabled=True)
        judge.should_trigger_for_error.return_value = False

        service = GatewayService(
            cm, engine, registry, MagicMock(), MagicMock(),
            llm_judge=judge,
        )

        async def operation(provider, opts):
            return await provider.search("test", opts)

        decision = RoutingDecision(policy_matched=None, content_type="search", provider="jina")

        await service._execute_with_fallback(
            "jina", ["searxng"], operation, MagicMock(),
            content_type="search", url=None, query="test",
            decision=decision,
        )

        judge.evaluate_for_retry.assert_not_called()
        assert decision.judge_invoked is False


import yaml  # noqa: E402


class TestConfigTestYaml:
    def test_judge_disabled_in_test_config(self):
        """config.test.yaml must have llm_judge.enabled = false."""
        with open("config.test.yaml") as f:
            config = yaml.safe_load(f)
        judge_config = config.get("llm_judge", {})
        assert judge_config.get("enabled") is False, (
            "llm_judge must be disabled in test config to avoid "
            "requiring LM Studio in CI"
        )
