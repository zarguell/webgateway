# Rebrand: webgateway → serp_llm

**Date:** 2026-06-29
**Status:** Draft

## Why

The project has outgrown its generic "webgateway" name. "serp_llm" (Search Engine
Results Page Orchestrator for LLMs) accurately describes what it does — it
orchestrates SERP retrieval and content extraction for AI agents.

The product is pre-release. There are no consumers to coordinate, no legacy
images to keep publishing, no API stability guarantees to maintain. This is the
right moment for a clean break.

## Naming Convention

| Context | Convention | Example |
|---|---|---|
| Python package (import) | `serp_llm` (PEP 8) | `from serp_llm.main import app` |
| PyPI project name | `serp-llm` (PEP 503) | `pip install serp-llm` |
| Docker resources | `serpllm-` (lowercase) | `serpllm-net`, `serpllm-data` |
| GHCR image | auto (repo name) | `ghcr.io/zarguell/serp_llm:*` |
| GitHub repo | `serp_llm` | `github.com/zarguell/serp_llm` |
| Display / title | "serpLLM" | FastAPI title, admin UI, docs |

## Scope

This is a pure rebrand — no code behavior changes, no refactoring, no feature
work. Every file that references "webgateway" / "WebGateway" is in scope.

**Total: ~130 files across 7 categories.**

## Inventory

### 1. Python package — ~90 files (60% of effort)

```
src/webgateway/                          → src/serp_llm/  (git mv)
pyproject.toml: name                     → "serp-llm"
pyproject.toml: packages                 → ["src/serp_llm"]
pyproject.toml: known-first-party        → ["serp_llm"]
Dockerfile CMD                           → uvicorn serp_llm.main:app
Dockerfile user/group                    → serpllm
All import statements                    → from serp_llm.xxx import YYY
All @patch("webgateway.xxx.yyy")        → @patch("serp_llm.xxx.yyy")
tests/__init__.py docstring             → "serpLLM"
```

Every Python file under `src/` and `tests/` imports from `webgateway.*`. Every
module docstring mentions "WebGateway". Both get bulk-replaced.

### 2. Docker / CI — 6 files, ~55 references

| File | Changes |
|---|---|
| `Dockerfile` | `groupadd/useradd` name, 8× `chown`, `USER`, `CMD` |
| `docker-compose.yml` | service name, 4× network refs, image tag |
| `docker-compose.test.yml` | service name, 2× network refs |
| `docker-compose.local.yml` | service name, 2× volume refs, 2× volume defs |
| `docker-compose.selfhosted.yml` | 6× container names, 8× network refs, 4× Traefik labels, image tag |
| `docker-compose.invisible-playwright.yml` | image tag |

The Docker network `webgateway-net` is renamed to `serpllm-net` everywhere.
Docker volumes `webgateway-data` and `webgateway-logs` → `serpllm-data`,
`serpllm-logs`. Container names `webgateway-*` → `serpllm-*`.

The CI workflow at `.github/workflows/docker-publish.yml` uses
`${{ github.repository }}` which will auto-resolve to `zarguell/serp_llm` once
the repo is renamed. No workflow changes needed.

### 3. GitHub — 1 click + 4 doc updates

| Action | Details |
|---|---|
| Rename repo on GitHub | `zarguell/webgateway` → `zarguell/serp_llm` |
| Update mkdocs.yml | `site_url`, `repo_url` |
| Update README.md | clone URL |
| Update self-hosted.md | clone URL |
| Update installation.md | clone URL |

GHCR images auto-follow the repo name. Old images at
`ghcr.io/zarguell/webgateway:*` are orphaned — delete from Package settings.

### 4. Documentation — ~15 files

| File | Changes |
|---|---|
| `README.md` | title, description, clone URL, badge URLs — full rewrite |
| `AGENTS.md` | heading, 8× `src/webgateway/` paths, compose commands |
| `docs-src/mkdocs.yml` | `site_name`, `site_url`, `repo_url` |
| `docs-src/docs/index.md` | heading |
| `docs-src/docs/**/*.md` (~9 pages) | project name, paths, code examples |
| `PRD.md`, `PRD-addendum*.md` (~5 files) | heading, body references |

### 5. Admin UI templates — 9 files

Each HTML template has a `<title>` tag containing "WebGateway Admin" and the
sidebar in `base.html` has an `<h1>WebGateway</h1>`. These are single-line
string replacements.

### 6. Application-internal branding — 6 references

```
main.py:                         FastAPI(title="serpLLM")
mcp/server.py:                   MCP server name → "serpLLM"
config.py:                       SMTP from_addr → "serpllm@..."
config.py:                       subject_prefix → "[serpLLM]"
audit.py:                        _LOGGER_NAME → "serp_llm.audit"
injection/classifier.py:         pip install hint → "serp-llm[injection]"
```

### 7. Shell scripts — 1 file

`scripts/ensure-gateway.sh`: 7× echo statements, 1× `docker compose logs`
reference. All "WebGateway" text → "serpLLM".

### 8. Historical plans — ~20 files (optional)

`docs/superpowers/plans/` and `docs/superpowers/specs/` contain internal design
documents that reference "webgateway". These are audit trail — they can be
bulk-replaced or left as-is. No functional impact either way.

## Execution Strategy

**Atomic branch, single commit, then repo rename.**

```
Branch: rebrand/serpllm

Step 1: git mv src/webgateway/ src/serp_llm/
Step 2: Bulk sed: webgateway → serp_llm (Python imports, test patches)
Step 3: Bulk sed: WebGateway → serpLLM (display names, titles)
Step 4: Bulk sed: webgateway- → serpllm- (Docker resources)
Step 5: Update pyproject.toml (name, packages, known-first-party)
Step 6: Update Dockerfile (user/group, chowns, CMD)
Step 7: Update all docker-compose*.yml files
Step 8: Update all HTML templates
Step 9: Update docs (README, AGENTS.md, mkdocs.yml, doc pages, PRDs)
Step 10: Update scripts/ensure-gateway.sh
Step 11: Update app-internal branding (main.py, config.py, mcp/server.py, etc.)
Step 12: ruff check --fix src/ tests/
Step 13: Run unit tests to verify nothing is broken
Step 14: Commit, push branch
Step 15: Rename GitHub repo: webgateway → serp_llm
Step 16: Push tags to trigger GHCR rebuild
Step 17: Verify: clone fresh, docker compose up, run a search
```

The branch has one commit titled `rebrand: webgateway → serp_llm (#N)`.

## Risk & Mitigation

| Risk | Likelihood | Mitigation |
|---|---|---|
| Missed import path | Medium | `ruff check` catches unresolved imports. Unit tests surface the rest. |
| Docker network name mismatch between compose files | Low | All 5 compose files updated in same commit. `docker compose up` fails fast. |
| GHCR orphan images | Certain | Delete old packages from GitHub UI post-rename. |
| Old Docker volumes with `webgateway` ownership | Medium | Fresh deploy on new machine — no old volumes exist. Local dev may need `docker volume rm`. |
| `docs/superpowers/` drift | Low | Acceptable. These are historical artifacts. |

## Verification

1. `ruff check src/ tests/` — zero errors
2. `pytest tests/unit/ -v` — all pass
3. `git diff --stat` — every changed file is expected (no stray renames)
4. Manual: `docker compose up -d --build` + `curl /health` — green
5. Manual: `git clone` the new repo URL on another machine — works
