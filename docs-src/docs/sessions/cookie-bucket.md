# Cookie Bucket Setup

The Cookie Bucket system stores encrypted browser sessions for authenticated web extraction.

## Create a Session

Admin API:

```bash
curl -X POST http://localhost:8080/admin/sessions/create \
  -H "Authorization: Bearer $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "wsj_session_1",
    "browser": "invisible_playwright",
    "domain": "wsj.com",
    "cookies": [...],
    "user_agent": "Mozilla/5.0...",
    "fingerprint_id": "fp_abc123"
  }'
```

Cookie values are encrypted at rest and never returned in list/status responses.

## Use in Extraction

```bash
curl -X POST http://localhost:8080/extract \
  -H "Authorization: Bearer $AGENT1_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://www.wsj.com/article",
    "session_profile": "wsj_session_1"
  }'
```
