# syntax=docker/dockerfile:1
FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/feder-cr/invisible_playwright"
LABEL description="REST API sidecar for invisible_playwright stealth Firefox"

# Install Firefox/Playwright system dependencies
# https://playwright.dev/docs/troubleshooting#system-dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libatk1.0-0t64 libatk-bridge2.0-0t64 \
    libcups2t64 libdrm2 libdbus-1-3 libxkbcommon0 \
    libxcomposite1 libxdamage1 libxrandr2 libgbm1 \
    libpango-1.0-0 libcairo2 libasound2t64 libxshmfence1 \
    libgtk-3-0t64 libgdk-pixbuf-2.0-0 \
    fonts-noto-color-emoji fonts-freefont-ttf xvfb \
    ca-certificates wget git \
    && rm -rf /var/lib/apt/lists/*

# Install invisible_playwright from GitHub + REST API dependencies
RUN pip install --no-cache-dir git+https://github.com/feder-cr/invisible_playwright.git uvicorn fastapi pydantic

# Pre-download the patched Firefox binary (~100 MB) — cached in the image layer
RUN python -m invisible_playwright fetch --force

# Copy the REST API server
COPY server.py /app/server.py
WORKDIR /app

EXPOSE 3001

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "3001", "--log-level", "info"]
