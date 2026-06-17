# Docker Compose Quickstart

## Basic Stack

```yaml
services:
  webgateway:
    build: .
    ports:
      - "8080:8080"
    volumes:
      - ./config.yaml:/app/config.yaml
      - ./logs:/app/logs
      - ./sessions:/app/sessions
      - ./data:/app/data
    env_file:
      - .env
```

## With Stealth Browser

```bash
docker compose --profile stealth up -d
```

## With VPN

```bash
docker compose --profile vpn up -d
```

See [Docker Compose Profiles](../architecture/compose-profiles.md) for all available profiles.
