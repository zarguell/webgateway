"""Alert dispatcher — routes event notifications to webhook and SMTP channels.

Fire-and-forget design: ``dispatch()`` never raises — delivery errors are
caught and logged as warnings so request handlers are never affected.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from serp_llm.config import AlertConfig

logger = logging.getLogger(__name__)

__all__ = ["AlertDispatcher"]


class AlertDispatcher:
    """Dispatches alert events to webhook and/or SMTP channels.

    Channels are configured via :class:`AlertConfig`. Only event types listed
    in ``config.events`` are dispatched; everything else is silently skipped.

    Delivery is fully fire-and-forget — all exceptions from webhook or SMTP
    are caught and logged, never propagated to callers.
    """

    def __init__(self, config: AlertConfig) -> None:
        self._config = config
        self._events: set[str] = set(config.events)
        self._suppress_seconds = config.suppress_seconds
        self._last_dispatch: dict[str, float] = {}
        self._client = httpx.AsyncClient(
            timeout=10,
            headers=config.webhook.headers,
        )

    def should_dispatch(self, event_type: str) -> bool:
        """Check if this event type is configured for dispatch."""
        return event_type in self._events

    async def dispatch(self, event_data: dict[str, Any]) -> None:
        """Dispatch event to all configured channels (webhook + SMTP).

        Runs each enabled channel in parallel via ``asyncio.gather`` with
        ``return_exceptions=True`` so one channel failing never affects
        another. This method never raises.

        Rate-limiting: identical events for the same provider are suppressed
        within ``suppress_seconds`` to prevent alert flooding (e.g. a quota-
        exhausted provider triggering on every request).
        """
        event_type = event_data.get("event", "")
        if not self.should_dispatch(event_type):
            return

        # Per-event+provider suppression to prevent flooding
        if self._suppress_seconds > 0:
            key = f"{event_type}:{event_data.get('provider', '')}"
            last = self._last_dispatch.get(key, 0.0)
            if time.monotonic() - last < self._suppress_seconds:
                return
            self._last_dispatch[key] = time.monotonic()
        tasks = []
        if self._config.webhook.url:
            tasks.append(self._send_webhook(event_data))
        if self._config.smtp.enabled and self._config.smtp.to_addrs:
            tasks.append(self._send_smtp(event_data))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _send_webhook(self, event_data: dict[str, Any]) -> None:
        """POST event JSON to the configured webhook URL."""
        try:
            resp = await self._client.post(self._config.webhook.url, json=event_data)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("Webhook delivery failed: %s", exc)

    async def _send_smtp(self, event_data: dict[str, Any]) -> None:
        """Send event notification via SMTP.

        ``aiosmtplib`` is imported lazily so the dependency is only needed
        when SMTP is actually configured.
        """
        from aiosmtplib import SMTP  # lazy import — avoids hard runtime dep

        cfg = self._config.smtp
        try:
            subject = f"{cfg.subject_prefix} {event_data.get('event', 'alert')}"
            body = self._format_email_body(event_data)
            message = (
                f"Subject: {subject}\nFrom: {cfg.from_addr}\n"
                f"To: {', '.join(cfg.to_addrs)}\n"
                f"Content-Type: text/plain; charset=utf-8\n\n{body}"
            )
            async with SMTP(
                hostname=cfg.host, port=cfg.port, use_tls=cfg.use_tls
            ) as smtp:
                if cfg.username and cfg.password:
                    await smtp.login(cfg.username, cfg.password)
                await smtp.sendmail(cfg.from_addr, cfg.to_addrs, message)
        except Exception as exc:
            logger.warning("SMTP delivery failed: %s", exc)

    @staticmethod
    def _format_email_body(event_data: dict[str, Any]) -> str:
        """Format event data into a readable plain-text email body."""
        lines = [f"Event: {event_data.get('event', 'unknown')}"]
        for key, val in event_data.items():
            if key != "event":
                lines.append(f"  {key}: {val}")
        return "\n".join(lines)

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
