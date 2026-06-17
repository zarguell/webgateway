# Bootstrap and First-Run Setup

When the gateway starts with an empty `api_keys` database, no API keys exist to authenticate requests. The bootstrap key mechanism solves this chicken-and-egg problem.

## First-Run Sequence

1. Set `BOOTSTRAP_ADMIN_KEY=<secret>` in `.env`
2. Start the gateway: `docker compose up -d`
3. Log into the Admin UI at `/admin/login` with the bootstrap key
4. Create a real admin key via the **API Keys** page
5. Copy the plaintext secret
6. Remove `BOOTSTRAP_ADMIN_KEY` from `.env`
7. The bootstrap key is now inert — continue with your real admin key

## Security Properties

- The bootstrap key is never written to the database
- Once any admin key has been created, the bootstrap key is rejected
- All bootstrap key usage is logged with `api_key_id: "bootstrap"`
- Operators should unset `BOOTSTRAP_ADMIN_KEY` after first admin key creation
