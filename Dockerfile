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

ARG ENABLE_INJECTION=0
RUN mkdir -p /app/models \
    && if [ "$ENABLE_INJECTION" = "1" ]; then \
      pip install --no-cache-dir '.[injection]' \
      && python scripts/fetch_injection_model.py || true; \
    else \
      pip install --no-cache-dir .; \
    fi

# Strip build-only packages that bloat the runtime image
RUN pip uninstall -y --no-cache-dir pip setuptools 2>/dev/null || true \
    && find /usr/local/lib/python3.12/site-packages \
         -maxdepth 1 -type d \
       \( -name "pip*" -o -name "setuptools*" -o -name "babel*" \
       -o -name "pkg_resources" \) \
       -exec rm -rf {} + 2>/dev/null || true

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

RUN groupadd -r webgateway && useradd -r -g webgateway -d /app -s /sbin/nologin webgateway

WORKDIR /app

RUN mkdir -p /app/data /app/logs /app/static /app/models

# COPY --chown avoids creating a duplicate layer (saves ~750MB vs COPY + chown)
COPY --from=builder --chown=webgateway:webgateway /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder --chown=webgateway:webgateway /usr/local/bin /usr/local/bin
COPY --from=builder --chown=webgateway:webgateway /app/models /app/models

COPY --chown=webgateway:webgateway src/ ./src/

# Copy MkDocs output
COPY --from=docs-builder /app/static/docs /app/static/docs
RUN chown webgateway:webgateway /app/static/docs

USER webgateway

EXPOSE 8080

CMD ["uvicorn", "webgateway.main:app", "--host", "0.0.0.0", "--port", "8080"]
