# Session Lifecycle

A session goes through these stages:

1. **Created** — Cookies imported, encrypted, and stored. Session is valid.
2. **Active** — Used in extract requests. `last_used_ts` updated on each use.
3. **Expired** — Session TTL reached. Automatic on read.
4. **Refreshed** — New cookies imported, preserving metadata.
5. **Invalidated** — Manual or automatic (on login wall detection).

## Auto-Invalidation

When `auto_invalidate_on_login_wall` is enabled, the gateway checks response content for login-wall patterns. If detected, the session is automatically invalidated and the request falls back to the next provider.

## Proxy Binding

With `strict_proxy_binding: true`, a session can only be used through the proxy it was created with. This prevents IP/cookie mismatches.
