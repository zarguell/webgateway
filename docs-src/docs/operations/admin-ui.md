# Admin UI Guide

The Admin UI is a lightweight, self-contained web interface at `/admin`. It uses Jinja2 templates with HTMX for dynamic updates and TailwindCSS for styling.

## Access

1. Navigate to `http://localhost:8080/admin/login`
2. Enter your admin API key
3. A session cookie is issued (24h TTL)

## Pages

### Dashboard
At-a-glance status: provider health grid, request volume sparkline, cache hit rate, active alerts.

### API Keys
List, create, and revoke API keys. Plaintext secret shown exactly once on creation.

### Providers
Provider health, circuit state, quota usage, and warnings. Manual circuit reset available.

### Sessions
Cookie Bucket management: list, create, invalidate, and refresh sessions.

### Usage
Per-provider and per-key usage statistics with selectable time ranges (24h/7d/30d).

### Logs
Live log viewer with auto-refresh (5s polling via HTMX). Filter by key ID, provider, status, and cache hit. Each entry expandable to full JSON.

### Cache
Cache statistics, invalidation by URL/domain/provider, and full flush with confirmation.

## Login Error Messages

- **Invalid API key or not an admin key** — The key doesn't exist or has `operator` role
- Keys with `operator` role cannot access the Admin UI
