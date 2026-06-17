# First API Call

## Search

```bash
curl -X POST http://localhost:8080/search \
  -H "Authorization: Bearer $AGENT1_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query": "what is the weather in London", "num_results": 3}'
```

## Extract (Content Extraction)

```bash
curl -X POST http://localhost:8080/extract \
  -H "Authorization: Bearer $AGENT1_KEY" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com"}'
```

## Dry Run (Preview Policy Decision)

```bash
curl -X POST "http://localhost:8080/search?dry_run=true" \
  -H "Authorization: Bearer $AGENT1_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query": "test", "provider": "searxng"}'
```

Returns the policy decision without executing the search.
