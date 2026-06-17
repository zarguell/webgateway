"""Unit tests for ProviderResourceManager — circuit breaker + quotas."""

from __future__ import annotations

import tempfile
import time

import pytest

from webgateway.config import GatewayConfig
from webgateway.resource_manager import ProviderResourceManager


def _make_manager(tmp_dir: str, overrides: dict | None = None) -> ProviderResourceManager:
    """Helper: create a ProviderResourceManager with given config overrides."""
    base = {
        "circuit_breaker": {
            "enabled": True,
            "providers": {
                "default": {
                    "error_threshold": 3,
                    "window_seconds": 60,
                    "cooldown_seconds": 10,
                },
            },
        },
        "quotas": {},
        "providers": {"test_provider": {"enabled": True}},
    }
    if overrides:
        _deep_merge(base, overrides)
    cfg = GatewayConfig.model_validate(base)
    return ProviderResourceManager(f"{tmp_dir}/test.db", cfg)


def _deep_merge(base: dict, override: dict) -> None:
    """Recursively merge override into base."""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


@pytest.fixture
def cb_manager(tmp_path) -> ProviderResourceManager:
    return _make_manager(str(tmp_path))


# ═══════════════════════════════════════════════════════════════════
# Circuit breaker — basic state machine
# ═══════════════════════════════════════════════════════════════════

class TestCircuitBreaker:

    async def test_starts_closed(self, cb_manager):
        state = await cb_manager.get_circuit_state("test_provider")
        assert state == "closed"

    async def test_remains_closed_below_threshold(self, cb_manager):
        await cb_manager.record_failure("test_provider", "timeout")
        await cb_manager.record_failure("test_provider", "timeout")
        state = await cb_manager.get_circuit_state("test_provider")
        assert state == "closed"

    async def test_trips_at_threshold(self, cb_manager):
        await cb_manager.record_failure("test_provider", "timeout")
        await cb_manager.record_failure("test_provider", "timeout")
        await cb_manager.record_failure("test_provider", "timeout")
        state = await cb_manager.get_circuit_state("test_provider")
        assert state == "open"

    async def test_success_resets_failure_count_when_closed(self, cb_manager):
        await cb_manager.record_failure("test_provider", "timeout")
        await cb_manager.record_failure("test_provider", "timeout")
        await cb_manager.record_success("test_provider")
        await cb_manager.record_failure("test_provider", "timeout")
        await cb_manager.record_failure("test_provider", "timeout")
        state = await cb_manager.get_circuit_state("test_provider")
        assert state == "closed"
        await cb_manager.record_failure("test_provider", "timeout")
        state = await cb_manager.get_circuit_state("test_provider")
        assert state == "open"

    async def test_admin_reset_closes_circuit(self, cb_manager):
        await cb_manager.record_failure("test_provider", "timeout")
        await cb_manager.record_failure("test_provider", "timeout")
        await cb_manager.record_failure("test_provider", "timeout")
        assert await cb_manager.get_circuit_state("test_provider") == "open"
        await cb_manager.reset_circuit("test_provider")
        assert await cb_manager.get_circuit_state("test_provider") == "closed"

    async def test_disabled_always_returns_closed(self):
        m = _make_manager(tempfile.mkdtemp(), {"circuit_breaker": {"enabled": False}})
        for _ in range(10):
            await m.record_failure("test_provider", "timeout")
        state = await m.get_circuit_state("test_provider")
        assert state == "closed"


# ═══════════════════════════════════════════════════════════════════
# Circuit breaker — sliding window
# ═══════════════════════════════════════════════════════════════════

class TestSlidingWindow:

    async def test_window_expiry_resets_failure_count(self):
        """Failures older than window_seconds should not count."""
        m = _make_manager(tempfile.mkdtemp(), {
            "circuit_breaker": {"providers": {"default": {
                "error_threshold": 3,
                "window_seconds": 1,
                "cooldown_seconds": 5,
            }}}
        })
        await m.record_failure("test_provider", "timeout")
        await m.record_failure("test_provider", "timeout")
        time.sleep(1.1)  # window expires
        await m.record_failure("test_provider", "timeout")
        state = await m.get_circuit_state("test_provider")
        # Window expired so failure_count should have reset to 1, not 3
        assert state == "closed", f"Expected closed (window reset), got {state}"


# ═══════════════════════════════════════════════════════════════════
# Circuit breaker — half-open / cooldown
# ═══════════════════════════════════════════════════════════════════

class TestHalfOpen:

    async def test_cooldown_transitions_to_half_open(self):
        m = _make_manager(tempfile.mkdtemp(), {
            "circuit_breaker": {"providers": {"default": {
                "error_threshold": 1,
                "window_seconds": 60,
                "cooldown_seconds": 1,
            }}}
        })
        await m.record_failure("test_provider", "timeout")
        assert await m.get_circuit_state("test_provider") == "open"
        time.sleep(1.1)
        state = await m.get_circuit_state("test_provider")
        assert state == "half_open", f"Expected half_open after cooldown, got {state}"

    async def test_half_open_success_closes_circuit(self):
        m = _make_manager(tempfile.mkdtemp(), {
            "circuit_breaker": {"providers": {"default": {
                "error_threshold": 1,
                "window_seconds": 60,
                "cooldown_seconds": 1,
            }}}
        })
        await m.record_failure("test_provider", "timeout")
        time.sleep(1.1)
        assert await m.get_circuit_state("test_provider") == "half_open"
        await m.record_success("test_provider")
        assert await m.get_circuit_state("test_provider") == "closed"

    async def test_half_open_failure_reopens(self):
        m = _make_manager(tempfile.mkdtemp(), {
            "circuit_breaker": {"providers": {"default": {
                "error_threshold": 1,
                "window_seconds": 60,
                "cooldown_seconds": 1,
            }}}
        })
        await m.record_failure("test_provider", "timeout")
        time.sleep(1.1)
        assert await m.get_circuit_state("test_provider") == "half_open"
        await m.record_failure("test_provider", "timeout")
        assert await m.get_circuit_state("test_provider") == "open"


# ═══════════════════════════════════════════════════════════════════
# filter_available
# ═══════════════════════════════════════════════════════════════════

class TestFilterAvailable:

    async def test_removes_open_providers(self, cb_manager):
        await cb_manager.record_failure("test_provider", "timeout")
        await cb_manager.record_failure("test_provider", "timeout")
        await cb_manager.record_failure("test_provider", "timeout")
        result = await cb_manager.filter_available(["test_provider", "other"])
        assert "other" in result
        assert "test_provider" not in result

    async def test_returns_empty_when_all_open(self):
        m = _make_manager(tempfile.mkdtemp(), {
            "circuit_breaker": {"providers": {"default": {
                "error_threshold": 1,
                "window_seconds": 60,
                "cooldown_seconds": 10,
            }}}
        })
        await m.record_failure("test_provider", "timeout")
        result = await m.filter_available(["test_provider"])
        assert result == []

    async def test_no_config_passes_all_through(self):
        m = _make_manager(tempfile.mkdtemp(), {
            "circuit_breaker": {"providers": {}},
            "quotas": {},
        })
        result = await m.filter_available(["a", "b"])
        assert result == ["a", "b"]


# ═══════════════════════════════════════════════════════════════════
# Quota tracking
# ═══════════════════════════════════════════════════════════════════

class TestQuotaTracking:

    async def test_no_quota_config_returns_zero_usage(self):
        m = _make_manager(tempfile.mkdtemp(), {"circuit_breaker": {"enabled": False}})
        info = await m.get_quota_info("test_provider")
        assert info["calls_month"] == 0
        assert info["limit_month"] is None
        assert info["pct_used"] == 0.0
        assert info["exhausted"] is False

    async def test_tracks_cumulative_usage(self):
        m = _make_manager(tempfile.mkdtemp(), {
            "circuit_breaker": {"enabled": False},
            "quotas": {"providers": {
                "limited": {
                    "monthly_limit": 100,
                    "alert_at_percent": 80,
                    "exhausted_action": "remove_from_pool",
                },
            }},
            "providers": {"limited": {"enabled": True}},
        })
        await m.record_usage("limited", "search", "req1", True, 100, cost_units=10)
        await m.record_usage("limited", "search", "req2", True, 200, cost_units=20)
        info = await m.get_quota_info("limited")
        assert info["calls_month"] == 30
        assert info["limit_month"] == 100
        assert info["pct_used"] == 30.0
        assert info["exhausted"] is False

    async def test_exhausted_remove_from_pool(self):
        m = _make_manager(tempfile.mkdtemp(), {
            "circuit_breaker": {"enabled": False},
            "quotas": {"providers": {
                "limited": {
                    "monthly_limit": 5,
                    "alert_at_percent": 80,
                    "exhausted_action": "remove_from_pool",
                },
            }},
            "providers": {"limited": {"enabled": True}, "other": {"enabled": True}},
        })
        await m.record_usage("limited", "search", "req1", True, 100, cost_units=5)
        result = await m.filter_available(["limited", "other"])
        assert "limited" not in result
        assert "other" in result

    async def test_exhausted_fallback_only(self):
        m = _make_manager(tempfile.mkdtemp(), {
            "circuit_breaker": {"enabled": False},
            "quotas": {"providers": {
                "limited": {
                    "monthly_limit": 5,
                    "alert_at_percent": 80,
                    "exhausted_action": "fallback_only",
                },
            }},
            "providers": {"limited": {"enabled": True}, "primary": {"enabled": True}},
        })
        await m.record_usage("limited", "search", "req1", True, 100, cost_units=5)
        result = await m.filter_available(["primary", "limited"])
        assert result == ["primary", "limited"], (
            f"Expected primary first, limited last, got {result}"
        )

    async def test_override_quota(self):
        m = _make_manager(tempfile.mkdtemp(), {
            "circuit_breaker": {"enabled": False},
            "quotas": {"providers": {
                "p1": {
                    "monthly_limit": 100,
                    "alert_at_percent": 80,
                    "exhausted_action": "remove_from_pool",
                },
            }},
            "providers": {"p1": {"enabled": True}},
        })
        await m.record_usage("p1", "search", "req1", True, 100, cost_units=80)
        await m.override_quota("p1", remaining=50)
        info = await m.get_quota_info("p1")
        assert info["calls_month"] == 50

    async def test_reset_quota(self):
        m = _make_manager(tempfile.mkdtemp(), {
            "circuit_breaker": {"enabled": False},
            "quotas": {"providers": {
                "p1": {
                    "monthly_limit": 100,
                    "alert_at_percent": 80,
                    "exhausted_action": "remove_from_pool",
                },
            }},
            "providers": {"p1": {"enabled": True}},
        })
        await m.record_usage("p1", "search", "req1", True, 100, cost_units=50)
        await m.reset_quota("p1")
        info = await m.get_quota_info("p1")
        assert info["calls_month"] == 0


# ═══════════════════════════════════════════════════════════════════
# get_summary
# ═══════════════════════════════════════════════════════════════════

class TestGetSummary:

    async def test_summary_includes_all_configured_providers(self, cb_manager):
        summary = await cb_manager.get_summary()
        assert "test_provider" in summary
        item = summary["test_provider"]
        assert item["circuit_state"] == "closed"
        assert item["calls_month"] == 0
        assert item["quota_pct"] == 0.0
