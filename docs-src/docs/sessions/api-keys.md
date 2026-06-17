# API Key Management

API keys are stored in a SQLite database with bcrypt-hashed secrets. Plaintext secrets are shown exactly once at creation time.

## Roles

| Role | Access |
|------|--------|
| `operator` | `POST /search`, `POST /extract` |
| `admin` | All endpoints including `/admin/*` and key management |

## Manage via Admin UI

1. Go to `/admin/keys`
2. Click **+ Create Key**
3. Choose label and role
4. Copy the plaintext secret (shown once)
5. To revoke, click **Revoke** on any active key

## Manage via API

```bash
# Create key
curl -X POST http://localhost:8080/admin/keys/create \
  -H "Authorization: Bearer $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"label": "my-agent", "role": "operator"}'

# List keys
curl http://localhost:8080/admin/keys \
  -H "Authorization: Bearer $ADMIN_KEY"

# Revoke key
curl -X POST http://localhost:8080/admin/keys/key_abc123/revoke \
  -H "Authorization: Bearer $ADMIN_KEY"
```

## Key Rotation

Create a new key, update your agent's config, then revoke the old key. Both keys are valid during the transition window.
