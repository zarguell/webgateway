# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Layer-split: install deps (heavy, slow) before copying source (changes every build).
# A minimal package stub lets pip resolve and install all dependencies from
# pyproject.toml alone.  This layer is cached by GHA as long as deps are unchanged.
COPY pyproject.toml .
RUN mkdir -p src/serp_llm && touch src/serp_llm/__init__.py
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir .

ARG ENABLE_INJECTION=0
RUN --mount=type=cache,target=/root/.cache/pip \
    if [ "$ENABLE_INJECTION" = "1" ]; then \
      pip install --no-cache-dir '.[injection]'; \
    fi

# Now overlay real source — reinstalls only the package, deps already cached
COPY src/ ./src/
COPY scripts/ ./scripts/
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir --force-reinstall --no-deps . \
    && mkdir -p /app/models \
    && if [ "$ENABLE_INJECTION" = "1" ]; then \
      python scripts/fetch_injection_model.py || true; \
    fi

# Strip build-only packages that bloat the runtime image
RUN pip uninstall -y --no-cache-dir pip setuptools 2>/dev/null || true \
    && find /usr/local/lib/python3.12/site-packages \
         -maxdepth 1 -type d \
        \( -name "pip*" -o -name "setuptools*" \
        -o -name "pkg_resources" \) \
        -exec rm -rf {} + 2>/dev/null || true

# ----------
# MkDocs builder stage
FROM python:3.12-slim AS docs-builder

WORKDIR /app
COPY docs-src/mkdocs.yml ./docs-src/mkdocs.yml

# Layer-split: pip install cached unless mkdocs config changes
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir mkdocs mkdocs-material

COPY scripts/ ./scripts/
COPY docs-src/ ./docs-src/

# Generate provider data policy pages from metadata
RUN python scripts/generate_provider_pages.py docs-src/docs/providers/policies

# Build MkDocs static site
RUN mkdocs build --config-file docs-src/mkdocs.yml --site-dir /app/static/docs

# ----------
FROM python:3.12-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -r serpllm && useradd -r -g serpllm -d /app -s /sbin/nologin serpllm

WORKDIR /app

RUN mkdir -p /app/data /app/logs /app/static /app/models && chown -R serpllm:serpllm /app/data /app/logs /app/static

# COPY --chown avoids creating a duplicate layer (saves ~750MB vs COPY + chown)
COPY --from=builder --chown=serpllm:serpllm /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder --chown=serpllm:serpllm /usr/local/bin /usr/local/bin
COPY --from=builder --chown=serpllm:serpllm /app/models /app/models

COPY --chown=serpllm:serpllm src/ ./src/

# Copy MkDocs output
COPY --from=docs-builder /app/static/docs /app/static/docs
RUN chown serpllm:serpllm /app/static/docs

COPY --chown=serpllm:serpllm entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

USER serpllm

EXPOSE 8080

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "serp_llm.main:app", "--host", "0.0.0.0", "--port", "8080"]
