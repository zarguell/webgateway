# .env Reference

All secrets live in `.env`, which is gitignored. Never commit secrets to version control.

```env
# --- Provider API Keys ---
JINA_API_KEY=
FIRECRAWL_API_KEY=
BRAVE_API_KEY=
TAVILY_API_KEY=
EXA_API_KEY=
ZYTE_API_KEY=

# --- Authentication ---
AGENT1_KEY=
ADMIN_KEY=
BOOTSTRAP_ADMIN_KEY=       # Remove after first admin key created

# --- Encryption ---
SESSION_ENCRYPTION_KEY=    # For cookie jar files at rest
ADMIN_SESSION_SECRET=      # For admin UI session cookies

# --- Optional: Self-hosted Firecrawl ---
BULL_AUTH_KEY=

# --- Optional: VPN ---
WIREGUARD_PRIVATE_KEY=
WIREGUARD_ADDRESSES=
WIREGUARD_DNS=1.1.1.1
```

Generate keys with:

```bash
openssl rand -hex 32
```
