.PHONY: test-integration test-integration-firecrawl integration-up integration-down integration-test test-unit lint install dev-install

PYTHON := python
VENV := .venv
ACTIVATE := source $(VENV)/bin/activate

install:
	$(PYTHON) -m venv $(VENV)
	$(ACTIVATE) && pip install -e ".[dev]"

dev-install: install

lint:
	$(ACTIVATE) && ruff check src/ tests/

test-unit:
	$(ACTIVATE) && pytest tests/ -v --ignore=tests/integration

# ── Integration test targets ──────────────────────────────────────────────────

integration-up:
	docker compose -f docker-compose.test.yml up -d --build
	@echo "Waiting for gateway + SearXNG..."
	@for i in $$(seq 1 60); do \
		curl -sf http://localhost:8080/health 2>/dev/null | grep -q '"healthy":true' && \
		echo "Stack is ready!" && exit 0; \
		sleep 2; \
	done; \
	echo "Stack failed to become ready"; docker compose -f docker-compose.test.yml logs; exit 1

integration-test:
	$(ACTIVATE) && pytest tests/integration/ -v

integration-down:
	docker compose -f docker-compose.test.yml down -v

# Full flow: start stack, run tests, tear down (even on failure)
test-integration: integration-up
	$(ACTIVATE) && pytest tests/integration/ -v; STATUS=$$?; \
	$(MAKE) integration-down; \
	exit $$STATUS

# Self-hosted Firecrawl: start full stack (7 containers), run all tests, tear down
test-integration-firecrawl:
	docker compose -f docker-compose.test.yml --profile firecrawl-selfhosted up -d --build
	@echo "Waiting for gateway + SearXNG + Firecrawl..."
	@for i in $$(seq 1 90); do \
		curl -sf http://localhost:8080/health 2>/dev/null | grep -q '"healthy":true' && \
		echo "Stack is ready!" && break; \
		sleep 3; \
	done
	$(ACTIVATE) && pytest tests/integration/ -v; STATUS=$$?; \
	docker compose -f docker-compose.test.yml --profile firecrawl-selfhosted down -v; \
	exit $$STATUS
