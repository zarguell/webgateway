# Circuit Breaker

Per-provider failure tracking prevents cascading failures when upstream providers are unavailable.

## States

| State | Behavior |
|-------|----------|
| **Closed** | Normal operation. Requests pass through. |
| **Open** | Requests are rejected without attempting the provider. Cooldown timer starts. |
| **Half-Open** | After cooldown, a single probe request is allowed. Success → Closed. Failure → Open. |

## Configuration

```yaml
circuit_breaker:
  enabled: true
  providers:
    default:
      error_threshold: 5
      window_seconds: 60
      cooldown_seconds: 120
      trip_on: ["429", "503", "timeout", "bot_detected"]
    zyte:
      error_threshold: 2
      cooldown_seconds: 300
```

## Manual Reset

Via Admin UI or API:

```bash
curl -X POST http://localhost:8080/admin/circuit/reset \
  -H "Authorization: Bearer $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"provider": "zyte"}'
```
