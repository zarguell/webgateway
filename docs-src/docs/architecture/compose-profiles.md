# Docker Compose Profiles

## Base Stack

```bash
docker compose up -d
```

Starts: serpllm + searxng

## With Stealth Browser

```bash
docker compose --profile stealth up -d
```

Adds: invisible-playwright (C++-patched Firefox 150)

## With VPN

```bash
docker compose --profile vpn up -d
```

Adds: gluetun (WireGuard VPN with HTTP proxy)

## With Browsers (All)

```bash
docker compose --profile browsers up -d
```

## With Self-Hosted Firecrawl

```bash
docker compose -f docker-compose.yml -f docker-compose.firecrawl.yml up -d
```

Starts 7 containers including Firecrawl, Redis, and related services.

## All Profiles Together

```bash
docker compose --profile stealth --profile vpn up -d
```
