# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml .
COPY src/ ./src/
COPY scripts/ ./scripts/
RUN pip install --no-cache-dir '.[injection]'
RUN python scripts/fetch_injection_model.py || true

# ----------
# MkDocs builder stage
FROM python:3.12-slim AS docs-builder

RUN pip install --no-cache-dir mkdocs mkdocs-material

WORKDIR /app
COPY docs-src/ ./docs-src/
COPY scripts/ ./scripts/

# Generate provider data policy pages from metadata
RUN python scripts/generate_provider_pages.py docs-src/docs/providers/policies

# Build MkDocs static site
RUN mkdocs build --config-file docs-src/mkdocs.yml --site-dir /app/static/docs

# ----------
FROM python:3.12-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -r webgateway && useradd -r -g webgateway -d /app -s /sbin/nologin webgateway

WORKDIR /app

RUN mkdir -p /app/data /app/logs /app/static /app/models

COPY --from=builder /app/models /app/models

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY src/ ./src/

# Copy MkDocs output
COPY --from=docs-builder /app/static/docs /app/static/docs

RUN chown -R webgateway:webgateway /app

USER webgateway

EXPOSE 8080

CMD ["uvicorn", "webgateway.main:app", "--host", "0.0.0.0", "--port", "8080"]
