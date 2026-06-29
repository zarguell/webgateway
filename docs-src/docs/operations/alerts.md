# Alerts

serpLLM can deliver real-time notifications when significant operational events occur — quota thresholds crossed, circuits tripped, quotas exhausted. Notifications are sent through two independent channels: **webhooks** (Slack, Discord, ntfy, or any HTTP endpoint) and **SMTP email**.

Both channels are optional. Configure one, both, or neither.

## Supported Events

| Event | Trigger |
|-------|---------|
| `quota_alert` | A provider's monthly usage crosses the `alert_at_percent` threshold (e.g. 80% of quota). |
| `circuit_open` | A provider's circuit breaker trips from Closed to Open after exceeding the error threshold. |
| `quota_exhausted` | A provider's monthly quota is fully consumed; the exhausted action (`remove_from_pool` or `fallback_only`) takes effect. |

Only events listed in `alerts.events` are dispatched. All others are silently ignored.

## Webhook Configuration

The webhook channel sends a JSON `POST` to the configured URL. Any service that accepts an incoming HTTP JSON payload works.

```yaml
alerts:
  events: [quota_alert, circuit_open, quota_exhausted]
  webhook:
    url: ${ALERT_WEBHOOK_URL}
    headers:
      Content-Type: application/json
```

The POST body is the full event dictionary, for example:

```json
{
  "event": "quota_alert",
  "provider": "brave",
  "pct_used": 85.0,
  "limit_month": 2000,
  "calls_month": 1700
}
```

### Slack

Create an [incoming webhook](https://api.slack.com/messaging/webhooks) and wrap the payload:

```yaml
alerts:
  events: [quota_alert, circuit_open]
  webhook:
    url: ${SLACK_WEBHOOK_URL}
    headers:
      Content-Type: application/json
```

Slack expects a `{"text": "..."}` shape. Use a middleware relay (e.g. a small Lambda or n8n flow) to transform the serpLLM payload into Slack's format, or use ntfy/Discord which accept raw text.

### Discord

Use a [channel webhook URL](https://support.discord.com/hc/en-us/articles/228383668):

```yaml
alerts:
  webhook:
    url: ${DISCORD_WEBHOOK_URL}
```

### ntfy

Self-hosted [ntfy](https://ntfy.sh/) works with no payload transformation:

```yaml
alerts:
  webhook:
    url: https://ntfy.example.com/serpllm-alerts
    headers:
      Title: serpLLM Alert
```

## SMTP Configuration

Email alerts are sent via an authenticated SMTP server. The `aiosmtplib` library is used internally with lazy imports.

```yaml
alerts:
  events: [quota_alert, circuit_exhausted]
  smtp:
    enabled: true
    host: ${SMTP_HOST}
    port: 587
    username: ${SMTP_USER}
    password: ${SMTP_PASS}
    use_tls: true
    from_addr: ${SMTP_FROM:-serpllm@localhost}
    to_addrs:
      - ops-team@example.com
      - oncall@example.com
    subject_prefix: "[serpLLM]"
```

Email subjects are prefixed with `subject_prefix` followed by the event type (e.g. `[serpLLM] quota_alert`). The body is a plain-text key-value rendering of the event data.

## Delivery Semantics

- **Best-effort, non-blocking.** Alert delivery failures (network errors, SMTP refusals) are caught and logged — they never crash a request or block the gateway pipeline.
- **Parallel dispatch.** When both webhook and SMTP are configured, deliveries run concurrently via `asyncio.gather`. A failure in one channel does not prevent delivery to the other.
- **No retries.** Each event is delivered exactly once per channel. If the endpoint is down, the alert is lost. For critical alerts, pair with a queue or monitoring system.
- **Rate-limited suppression.** Identical events for the same provider are suppressed within `suppress_seconds` (default 300s / 5 minutes) to prevent flooding. A quota-exhausted provider that triggers on every request will only fire one alert per 5-minute window. Set to `0` to disable suppression.

## Full Configuration Reference

```yaml
alerts:
  # Events that trigger notifications.
  # Only listed events are dispatched; all others are ignored.
  events: [quota_alert, circuit_open, quota_exhausted]

  # Minimum seconds between identical alerts for the same provider.
  # Prevents flooding when a quota-exhausted provider triggers on every request.
  suppress_seconds: 300

  webhook:
    # Target URL for HTTP POST. Omit or set to null to disable webhook.
    url: ${ALERT_WEBHOOK_URL}
    # Custom headers added to every POST request.
    headers:
      Content-Type: application/json

  smtp:
    # Master switch for email delivery.
    enabled: false
    # SMTP server hostname.
    host: ${SMTP_HOST}
    # SMTP port (587 for STARTTLS, 465 for implicit TLS).
    port: 587
    # SMTP username. Use ${ENV_VAR} interpolation — never hardcode.
    username: ${SMTP_USER}
    # SMTP password. Use ${ENV_VAR} interpolation — never hardcode.
    password: ${SMTP_PASS}
    # Whether to use TLS (STARTTLS for port 587).
    use_tls: true
    # Sender email address.
    from_addr: ${SMTP_FROM:-serpllm@localhost}
    # Recipient list. Each address receives a copy.
    to_addrs:
      - admin@example.com
    # Prefix prepended to every email subject.
    subject_prefix: "[serpLLM]"
```

### Security

Secrets (`password`, `username`, webhook URLs containing tokens) must always be set via `${ENV_VAR}` interpolation in `config.yaml`. Define them in your `.env` file or Docker environment — never commit credentials to source control. See the [.env reference](../configuration/env-reference.md) for all supported variables.
