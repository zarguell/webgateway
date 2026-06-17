# Quota Management

Per-provider usage limits prevent unexpected API costs.

## Configuration

```yaml
quotas:
  brave:
    monthly_limit: 2000
    daily_limit: 200
    exhausted_action: fallback_only
  tavily:
    monthly_limit: 1000
    exhausted_action: remove_from_pool
```

## Exhausted Actions

| Action | Description |
|--------|-------------|
| `remove_from_pool` | Provider is excluded from routing entirely |
| `fallback_only` | Provider used only if no other provider is available |

## Reset

Via Admin UI or API:

```bash
curl -X POST http://localhost:8080/admin/quota/reset \
  -H "Authorization: Bearer $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"provider": "tavily"}'
```

## Override

Set remaining quota to a specific value:

```bash
curl -X POST http://localhost:8080/admin/quota/override \
  -H "Authorization: Bearer $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"provider": "brave", "remaining": 500}'
```
