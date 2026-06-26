"""Tests for configuration models — rate limiting section."""

from webgateway.config import GatewayConfig


def test_rate_limit_config_defaults():
    """Default rate limit config has sensible values and is disabled."""
    config = GatewayConfig()
    rl = config.rate_limiting
    assert rl.enabled is False
    assert rl.by_key.requests == 60
    assert rl.by_key.window_seconds == 60
    assert rl.by_ip.requests == 30
    assert rl.by_ip.window_seconds == 60
    assert rl.cleanup_interval_seconds == 300


def test_rate_limit_config_custom():
    """Custom values override defaults."""
    config = GatewayConfig.model_validate({
        "rate_limiting": {
            "enabled": True,
            "by_key": {"requests": 100, "window_seconds": 120},
        }
    })
    rl = config.rate_limiting
    assert rl.enabled is True
    assert rl.by_key.requests == 100
    assert rl.by_key.window_seconds == 120
    assert rl.by_ip.requests == 30
