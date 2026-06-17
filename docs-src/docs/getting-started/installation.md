# Installation

## Prerequisites

- Docker & Docker Compose v2
- Git
- An API key for at least one search/extract provider (optional for SearXNG)

## Clone & Configure

```bash
git clone <repo-url> webgateway
cd webgateway
cp .env.example .env
```

Edit `.env` with your provider API keys. At minimum, set one of `BRAVE_API_KEY`, `TAVILY_API_KEY`, or `JINA_API_KEY`.

## Start the Gateway

```bash
docker compose up -d
```

This starts the gateway on port `8080` with SearXNG as the default search provider.

Verify it's running:

```bash
curl http://localhost:8080/health
```

## Local Development

```bash
make install            # creates .venv, installs dev deps
source .venv/bin/activate
uvicorn webgateway.main:app --reload
```
