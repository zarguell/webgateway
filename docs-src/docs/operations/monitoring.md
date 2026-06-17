# Monitoring and Alerting

## Audit Log

Every request is logged to `logs/gateway.jsonl` in JSON Lines format. Each entry includes request ID, API key ID, provider, latency, cache status, DLP actions, and session info.

```json
{
  "request_id": "req_a1b2c3d4",
  "api_key_id": "key_agent1",
  "type": "search",
  "provider_used": "brave",
  "latency_ms": 450,
  "status": "success",
  "cache_hit": false,
  "ts": "2026-06-17T12:00:00+00:00"
}
```

## Events Log

Significant events (circuit trips, quota alerts, session invalidations) are written to `logs/events.jsonl`.

## Alerts

Configure a webhook URL in `config.yaml`:

```yaml
alerts:
  webhook_url: ${ALERT_WEBHOOK_URL}
  events: [quota_alert, circuit_open, quota_exhausted]
```

## Health Endpoint

```bash
curl http://localhost:8080/health
```

Returns provider health status, circuit states, and quota usage.
