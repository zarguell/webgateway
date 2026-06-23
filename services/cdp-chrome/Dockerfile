# syntax=docker/dockerfile:1
FROM python:3.12-slim

LABEL org.opencontainers.image.description="CDP Chrome sidecar — lightweight bridge to host Chrome via Chrome DevTools Protocol"

# socat forwards TCP from the container to the host's Chrome CDP endpoint,
# because Chrome only binds to 127.0.0.1 (unreachable from Docker directly).
RUN apt-get update && apt-get install -y --no-install-recommends socat \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir playwright uvicorn fastapi pydantic

COPY server.py /app/server.py
WORKDIR /app

EXPOSE 9222

ENV CDP_RELAY_PORT=9224

# Start socat relay in background, then start uvicorn
CMD socat TCP-LISTEN:${CDP_RELAY_PORT},fork,reuseaddr TCP:host.docker.internal:9222 & \
    uvicorn server:app --host 0.0.0.0 --port 9222 --log-level info
