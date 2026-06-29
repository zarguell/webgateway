"""Unit tests for AlertDispatcher — webhook and SMTP alert delivery."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from serp_llm.alerting import AlertDispatcher
from serp_llm.config import AlertConfig, SmtpConfig, WebhookConfig

# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════


def _webhook_config(
    url: str = "https://hooks.example.com/alert",
    headers: dict[str, str] | None = None,
) -> WebhookConfig:
    return WebhookConfig(
        url=url,
        headers=headers or {"Content-Type": "application/json"},
    )


def _smtp_config(to_addrs: list[str] | None = None, **overrides) -> SmtpConfig:
    defaults: dict = dict(
        enabled=True,
        host="smtp.example.com",
        port=587,
        username="alerts@example.com",
        password="secret",
        use_tls=True,
        from_addr="serp_llm@example.com",
        to_addrs=to_addrs if to_addrs is not None else ["admin@example.com"],
        subject_prefix="[serpLLM]",
    )
    defaults.update(overrides)
    return SmtpConfig(**defaults)


def _alert_config(
    events: list[str] | None = None,
    webhook: WebhookConfig | None = None,
    smtp: SmtpConfig | None = None,
) -> AlertConfig:
    return AlertConfig(
        events=events if events is not None else ["quota_alert", "circuit_open"],
        webhook=webhook or WebhookConfig(),
        smtp=smtp or SmtpConfig(),
    )


@pytest.fixture
def mock_httpx_client() -> MagicMock:
    """A mock httpx.AsyncClient whose .post is an AsyncMock."""
    client = MagicMock()
    client.post = AsyncMock()
    client.aclose = AsyncMock()
    return client


@pytest.fixture
def mock_smtp_instance() -> AsyncMock:
    """A mock aiosmtplib.SMTP instance (supports async context manager too)."""
    instance = AsyncMock()
    instance.__aenter__.return_value = instance
    instance.__aexit__.return_value = None
    return instance


# ═══════════════════════════════════════════════════════════════════
# should_dispatch
# ═══════════════════════════════════════════════════════════════════


class TestShouldDispatch:

    def test_returns_true_for_configured_event(self):
        dispatcher = AlertDispatcher(_alert_config(events=["quota_alert"]))
        assert dispatcher.should_dispatch("quota_alert") is True

    def test_returns_false_for_unconfigured_event(self):
        dispatcher = AlertDispatcher(_alert_config(events=["quota_alert"]))
        assert dispatcher.should_dispatch("unknown_event") is False

    def test_empty_events_list_dispatches_nothing(self):
        dispatcher = AlertDispatcher(_alert_config(events=[]))
        assert dispatcher.should_dispatch("quota_alert") is False

    def test_multiple_configured_events(self):
        dispatcher = AlertDispatcher(
            _alert_config(events=["quota_alert", "circuit_open", "quota_exhausted"])
        )
        assert dispatcher.should_dispatch("quota_alert") is True
        assert dispatcher.should_dispatch("circuit_open") is True
        assert dispatcher.should_dispatch("quota_exhausted") is True


# ═══════════════════════════════════════════════════════════════════
# dispatch — no channels configured
# ═══════════════════════════════════════════════════════════════════


class TestDispatchNoChannels:

    async def test_no_channels_no_errors(self, mock_httpx_client):
        """Empty webhook + disabled SMTP should not raise or make any calls."""
        dispatcher = AlertDispatcher(_alert_config(events=["quota_alert"]))
        dispatcher._client = mock_httpx_client
        await dispatcher.dispatch({"event": "quota_alert", "message": "test"})
        mock_httpx_client.post.assert_not_called()


# ═══════════════════════════════════════════════════════════════════
# dispatch — webhook only
# ═══════════════════════════════════════════════════════════════════


class TestDispatchWebhook:

    async def test_webhook_posted_with_full_payload(self, mock_httpx_client):
        cfg = _alert_config(events=["quota_alert"], webhook=_webhook_config())
        dispatcher = AlertDispatcher(cfg)
        dispatcher._client = mock_httpx_client

        event_data = {
            "event": "quota_alert",
            "provider": "brave",
            "pct_used": 85.0,
            "limit_month": 2000,
            "calls_month": 1700,
        }
        await dispatcher.dispatch(event_data)

        mock_httpx_client.post.assert_called_once()
        call = mock_httpx_client.post.call_args
        # URL is the first positional arg
        assert call.args[0] == cfg.webhook.url
        # Full event dict sent as JSON body
        assert call.kwargs["json"] == event_data

    async def test_webhook_not_called_when_url_is_none(self, mock_httpx_client):
        cfg = _alert_config(
            events=["quota_alert"],
            webhook=WebhookConfig(url=None),
        )
        dispatcher = AlertDispatcher(cfg)
        dispatcher._client = mock_httpx_client
        await dispatcher.dispatch({"event": "quota_alert"})
        mock_httpx_client.post.assert_not_called()


# ═══════════════════════════════════════════════════════════════════
# dispatch — SMTP only
# ═══════════════════════════════════════════════════════════════════


class TestDispatchSmtp:

    async def test_smtp_sendmail_called_with_correct_args(self, mock_smtp_instance):
        cfg = _alert_config(
            events=["quota_alert"],
            smtp=_smtp_config(to_addrs=["admin@example.com", "ops@example.com"]),
        )
        dispatcher = AlertDispatcher(cfg)

        event_data = {
            "event": "quota_alert",
            "provider": "brave",
            "pct_used": 90.0,
        }
        with patch("aiosmtplib.SMTP", return_value=mock_smtp_instance):
            await dispatcher.dispatch(event_data)

        mock_smtp_instance.sendmail.assert_called_once()
        call = mock_smtp_instance.sendmail.call_args
        # from_addr is first positional arg
        assert call.args[0] == cfg.smtp.from_addr
        # to_addrs is second positional arg
        assert call.args[1] == cfg.smtp.to_addrs
        # message body (third arg) contains the event type
        assert "quota_alert" in call.args[2]

    async def test_smtp_not_called_when_disabled(self, mock_smtp_instance):
        cfg = _alert_config(
            events=["quota_alert"],
            smtp=SmtpConfig(enabled=False),
        )
        dispatcher = AlertDispatcher(cfg)
        with patch("aiosmtplib.SMTP", return_value=mock_smtp_instance):
            await dispatcher.dispatch({"event": "quota_alert"})
        mock_smtp_instance.sendmail.assert_not_called()

    async def test_smtp_not_called_when_no_recipients(self, mock_smtp_instance):
        cfg = _alert_config(
            events=["quota_alert"],
            smtp=_smtp_config(to_addrs=[]),
        )
        dispatcher = AlertDispatcher(cfg)
        with patch("aiosmtplib.SMTP", return_value=mock_smtp_instance):
            await dispatcher.dispatch({"event": "quota_alert"})
        mock_smtp_instance.sendmail.assert_not_called()


# ═══════════════════════════════════════════════════════════════════
# dispatch — both channels
# ═══════════════════════════════════════════════════════════════════


class TestDispatchBothChannels:

    async def test_both_channels_called(
        self, mock_httpx_client, mock_smtp_instance
    ):
        cfg = _alert_config(
            events=["quota_alert"],
            webhook=_webhook_config(),
            smtp=_smtp_config(),
        )
        dispatcher = AlertDispatcher(cfg)
        dispatcher._client = mock_httpx_client

        event_data = {"event": "quota_alert", "provider": "brave", "pct_used": 95.0}
        with patch("aiosmtplib.SMTP", return_value=mock_smtp_instance):
            await dispatcher.dispatch(event_data)

        mock_httpx_client.post.assert_called_once()
        mock_smtp_instance.sendmail.assert_called_once()


# ═══════════════════════════════════════════════════════════════════
# dispatch — unconfigured event type
# ═══════════════════════════════════════════════════════════════════


class TestDispatchUnconfiguredEvent:

    async def test_unconfigured_event_makes_no_calls(
        self, mock_httpx_client, mock_smtp_instance
    ):
        cfg = _alert_config(
            events=["quota_alert"],
            webhook=_webhook_config(),
            smtp=_smtp_config(),
        )
        dispatcher = AlertDispatcher(cfg)
        dispatcher._client = mock_httpx_client

        with patch("aiosmtplib.SMTP", return_value=mock_smtp_instance):
            await dispatcher.dispatch({"event": "totally_unknown", "message": "hi"})

        mock_httpx_client.post.assert_not_called()
        mock_smtp_instance.sendmail.assert_not_called()

    async def test_missing_event_key_makes_no_calls(self, mock_httpx_client):
        cfg = _alert_config(events=["quota_alert"], webhook=_webhook_config())
        dispatcher = AlertDispatcher(cfg)
        dispatcher._client = mock_httpx_client
        # No "event" key at all — defaults to "" which is not in events
        await dispatcher.dispatch({"message": "something happened"})
        mock_httpx_client.post.assert_not_called()


# ═══════════════════════════════════════════════════════════════════
# Failure handling — exceptions must not propagate
# ═══════════════════════════════════════════════════════════════════


class TestWebhookFailure:

    async def test_webhook_error_does_not_propagate(self, mock_httpx_client):
        """If httpx raises during POST, dispatch must not raise."""
        mock_httpx_client.post.side_effect = ConnectionError("connection refused")
        cfg = _alert_config(events=["quota_alert"], webhook=_webhook_config())
        dispatcher = AlertDispatcher(cfg)
        dispatcher._client = mock_httpx_client

        # Should complete without raising
        await dispatcher.dispatch({"event": "quota_alert", "message": "test"})

    async def test_webhook_http_status_error_does_not_propagate(
        self, mock_httpx_client
    ):
        import httpx

        mock_httpx_client.post.side_effect = httpx.HTTPStatusError(
            "server error",
            request=MagicMock(),
            response=MagicMock(status_code=500),
        )
        cfg = _alert_config(events=["quota_alert"], webhook=_webhook_config())
        dispatcher = AlertDispatcher(cfg)
        dispatcher._client = mock_httpx_client

        await dispatcher.dispatch({"event": "quota_alert", "message": "test"})


class TestSmtpFailure:

    async def test_smtp_error_does_not_propagate(self, mock_smtp_instance):
        """If aiosmtplib raises during sendmail, dispatch must not raise."""
        mock_smtp_instance.sendmail.side_effect = RuntimeError("SMTP refused")
        cfg = _alert_config(events=["quota_alert"], smtp=_smtp_config())
        dispatcher = AlertDispatcher(cfg)

        with patch("aiosmtplib.SMTP", return_value=mock_smtp_instance):
            await dispatcher.dispatch({"event": "quota_alert", "message": "test"})

    async def test_smtp_connection_error_does_not_propagate(
        self, mock_smtp_instance
    ):
        mock_smtp_instance.connect.side_effect = ConnectionRefusedError("port closed")
        cfg = _alert_config(events=["quota_alert"], smtp=_smtp_config())
        dispatcher = AlertDispatcher(cfg)

        with patch("aiosmtplib.SMTP", return_value=mock_smtp_instance):
            await dispatcher.dispatch({"event": "quota_alert", "message": "test"})

    async def test_webhook_failure_does_not_block_smtp(
        self, mock_httpx_client, mock_smtp_instance
    ):
        """When webhook fails, SMTP delivery should still succeed (parallel gather)."""
        mock_httpx_client.post.side_effect = ConnectionError("webhook down")
        cfg = _alert_config(
            events=["quota_alert"],
            webhook=_webhook_config(),
            smtp=_smtp_config(),
        )
        dispatcher = AlertDispatcher(cfg)
        dispatcher._client = mock_httpx_client

        with patch("aiosmtplib.SMTP", return_value=mock_smtp_instance):
            await dispatcher.dispatch({"event": "quota_alert", "message": "test"})

        # SMTP still delivered despite webhook failure
        mock_smtp_instance.sendmail.assert_called_once()


# ═══════════════════════════════════════════════════════════════════
# _format_email_body
# ═══════════════════════════════════════════════════════════════════


class TestFormatEmailBody:

    def test_includes_all_event_keys(self):
        body = AlertDispatcher._format_email_body({
            "event": "quota_alert",
            "provider": "brave",
            "pct_used": 85.0,
        })
        assert "quota_alert" in body
        assert "brave" in body
        assert "85" in body

    def test_returns_plain_text_string(self):
        body = AlertDispatcher._format_email_body({"event": "circuit_open"})
        assert isinstance(body, str)

    def test_empty_dict_returns_string(self):
        body = AlertDispatcher._format_email_body({})
        assert isinstance(body, str)

    def test_nested_values_rendered(self):
        body = AlertDispatcher._format_email_body({
            "event": "quota_exhausted",
            "details": {"limit": 1000, "used": 1000},
        })
        assert "quota_exhausted" in body


# ═══════════════════════════════════════════════════════════════════
# close
# ═══════════════════════════════════════════════════════════════════


class TestClose:

    async def test_close_calls_aclose(self, mock_httpx_client):
        dispatcher = AlertDispatcher(_alert_config())
        dispatcher._client = mock_httpx_client
        await dispatcher.close()
        mock_httpx_client.aclose.assert_called_once()


# ═══════════════════════════════════════════════════════════════════
# suppress_seconds rate limiting
# ═══════════════════════════════════════════════════════════════════


class TestSuppressSeconds:

    @pytest.fixture
    def dispatcher(self):
        cfg = _alert_config(
            events=["quota_alert", "circuit_open", "injection_detected"],
            webhook=_webhook_config(),
        )
        cfg.suppress_seconds = 60
        d = AlertDispatcher(cfg)
        d._client = MagicMock()
        d._client.post = AsyncMock()
        return d

    async def test_first_dispatch_goes_through(self, dispatcher):
        await dispatcher.dispatch(
            {"event": "quota_alert", "provider": "exa", "pct_used": 90.0}
        )
        dispatcher._client.post.assert_called_once()

    async def test_rapid_second_dispatch_suppressed(self, dispatcher):
        await dispatcher.dispatch(
            {"event": "quota_alert", "provider": "exa", "pct_used": 90.0}
        )
        await dispatcher.dispatch(
            {"event": "quota_alert", "provider": "exa", "pct_used": 92.0}
        )
        dispatcher._client.post.assert_called_once()

    async def test_different_provider_not_suppressed(self, dispatcher):
        await dispatcher.dispatch(
            {"event": "quota_alert", "provider": "exa", "pct_used": 90.0}
        )
        await dispatcher.dispatch(
            {"event": "quota_alert", "provider": "brave", "pct_used": 85.0}
        )
        assert dispatcher._client.post.call_count == 2

    async def test_different_event_not_suppressed(self, dispatcher):
        await dispatcher.dispatch(
            {"event": "quota_alert", "provider": "exa", "pct_used": 90.0}
        )
        await dispatcher.dispatch(
            {"event": "circuit_open", "provider": "exa", "error_class": "429"}
        )
        assert dispatcher._client.post.call_count == 2

    async def test_suppress_zero_allows_all(self, mock_httpx_client):
        cfg = _alert_config(
            events=["quota_alert"],
            webhook=_webhook_config(),
        )
        cfg.suppress_seconds = 0
        d = AlertDispatcher(cfg)
        d._client = mock_httpx_client
        await d.dispatch(
            {"event": "quota_alert", "provider": "exa", "pct_used": 90.0}
        )
        await d.dispatch(
            {"event": "quota_alert", "provider": "exa", "pct_used": 92.0}
        )
        assert d._client.post.call_count == 2

    async def test_event_without_provider_uses_event_only_key(self, dispatcher):
        await dispatcher.dispatch(
            {"event": "injection_detected", "url": "https://evil.com"}
        )
        await dispatcher.dispatch(
            {"event": "injection_detected", "url": "https://evil2.com"}
        )
        dispatcher._client.post.assert_called_once()
