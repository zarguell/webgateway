# Cache Management

The response cache stores search and extract results to reduce latency and API costs.

## Configuration

```yaml
cache:
  enabled: true
  backend: sqlite
  default_ttl: 300
  rules:
    - match:
        domain_glob: ["*.wikipedia.org"]
      ttl: 43200
    - match:
        content_type: search
        provider: [brave, tavily]
      ttl: 120
```

## Cache Keys

Derived from content type + provider + query (search) or URL (extract). Cache entries are opaque JSON blobs.

## Invalidation

Automatic invalidations trigger on:
- Content too short (`content_length_bytes < threshold`)
- Bot detection patterns in response
- Provider errors (403, 429, bot_detected)

Manual invalidation via Admin UI or API:

```bash
curl -X POST http://localhost:8080/admin/cache/invalidate \
  -H "Authorization: Bearer $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/page"}'
```

## Flush

Delete all cache entries:

```bash
curl -X POST http://localhost:8080/admin/cache/flush \
  -H "Authorization: Bearer $ADMIN_KEY"
```
