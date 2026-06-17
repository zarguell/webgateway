# Bootstrap Admin Key Setup

On first startup with an empty `api_keys` table, the gateway cannot authenticate any request — including the request needed to create the first admin key. The bootstrap key solves this.

## Setup

1. Generate a bootstrap key:

   ```bash
   openssl rand -hex 32
   ```

2. Add it to `.env`:

   ```env
   BOOTSTRAP_ADMIN_KEY=<generated-secret>
   ```

3. Start the gateway:

   ```bash
   docker compose up -d
   ```

4. Log into the Admin UI at `http://localhost:8080/admin/login` with the bootstrap key.

5. Create a real admin key via the **API Keys** page in the Admin UI.

6. Copy the plaintext secret (shown exactly once).

7. Remove `BOOTSTRAP_ADMIN_KEY` from `.env`.

8. Continue using the Admin UI with your real admin key.

## How It Works

- The bootstrap key is **never written to the database**
- Once any admin key has been created, the bootstrap key is rejected even if still set
- Bootstrap key usage is always logged with `api_key_id: "bootstrap"`
