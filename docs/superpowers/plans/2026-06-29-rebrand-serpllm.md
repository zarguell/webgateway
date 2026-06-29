# serpLLM Rebrand Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebrand the entire project from "serp_llm" to "serp_llm" — Python package, Docker infrastructure, docs, UI, scripts, config, and CI — as one atomic change.

**Architecture:** Pure mechanical rename. One branch, one commit. `git mv src/serp_llm/ src/serp_llm/` → bulk sed across all file types → domain-specific file edits → ruff fix → unit tests → commit.

**Tech Stack:** Python, Docker Compose, MkDocs, Jinja2 templates, FastAPI

**Spec:** `docs/superpowers/specs/2026-06-29-rebrand-serpllm-design.md`

---

### Task 1: Python package rename (git mv + bulk sed)

**Files:** All `.py` files under `src/`, `tests/`, plus `pyproject.toml`, `Dockerfile`

**Naming map:**
| Search | Replace | Context |
|---|---|---|
| `serp_llm` (in imports/paths) | `serp_llm` | Python module paths, `@patch` strings |
| `serpLLM` (in display strings) | `serpLLM` | Docstrings, FastAPI title, MCP name |
| `serpllm-` (in Docker names) | `serpllm-` | Networks, volumes, container names, images |
| `serp_llm` (in system user) | `serpllm` | Dockerfile user/group |

- [ ] **Step 1: Create branch and move package directory**

```bash
git checkout -b rebrand/serpllm
git mv src/serp_llm src/serp_llm
```

- [ ] **Step 2: Bulk sed across all Python files — module paths**

Replace `serp_llm` → `serp_llm` but ONLY in import/path contexts. This covers:
- `from serp_llm.xxx import YYY` → `from serp_llm.xxx import YYY`
- `import serp_llm.xxx` → `import serp_llm.xxx`
- `@patch("serp_llm.xxx.yyy")` → `@patch("serp_llm.xxx.yyy")`
- `serp_llm.main:app` → `serp_llm.main:app`

```bash
# Python imports and module paths
find src/serp_llm tests -name '*.py' -exec sed -i '' 's/\bserp_llm\b/serp_llm/g' {} +
```

- [ ] **Step 3: Bulk sed — display names**

Replace `serpLLM` → `serpLLM` in display/title contexts (docstrings, FastAPI title, MCP server name, HTML titles)

```bash
find src/serp_llm tests -name '*.py' -exec sed -i '' 's/serpLLM/serpLLM/g' {} +
```

- [ ] **Step 4: Update pyproject.toml**

```
name = "serp-llm"                    (was: serp_llm)
packages = ["src/serp_llm"]          (was: src/serp_llm)
known-first-party = ["serp_llm"]     (was: serp_llm)
```

- [ ] **Step 5: Update Dockerfile**

Replace: user/group `serp_llm` → `serpllm`, CMD module path `serp_llm.main:app`
Also update docker-compose files (see Task 2)

- [ ] **Step 6: Fix the `docker build` reference in AGENTS.md**

Line 135: `docker build --build-arg ENABLE_INJECTION=1 -t serp_llm .` → `-t serpllm .`

- [ ] **Step 7: Run ruff to fix any import ordering**

```bash
source .venv/bin/activate && ruff check --fix src/serp_llm/ tests/
```

### Task 2: Docker infrastructure

**Files:** `Dockerfile`, `docker-compose.yml`, `docker-compose.test.yml`, `docker-compose.local.yml`, `docker-compose.selfhosted.yml`, `docker-compose.invisible-playwright.yml`

**Domain:** Each compose file gets unique replacements. No conflicts with Task 1 since compose files aren't Python.

- [ ] **Step 1: Update Dockerfile non-Python branding**

```
groupadd:  serp_llm → serpllm
useradd:   serp_llm → serpllm
USER:      serp_llm → serpllm
chown:     serp_llm → serpllm (8 occurrences)
```

- [ ] **Step 2: Update docker-compose.yml**

```
service name:  serp_llm → serpllm
network:       serpllm-net → serpllm-net (5 occurrences)
image:         serp_llm/invisible-playwright → serpllm/invisible-playwright
```

- [ ] **Step 3: Update docker-compose.test.yml**

```
service name:  serp_llm → serpllm
network:       serpllm-net → serpllm-net (2 occurrences)
```

- [ ] **Step 4: Update docker-compose.local.yml**

```
service name:  serp_llm → serpllm
volumes:       serpllm-data → serpllm-data, serpllm-logs → serpllm-logs
```

- [ ] **Step 5: Update docker-compose.selfhosted.yml**

```
container names:  serpllm-* → serpllm-* (6 occurrences)
network:          serpllm-net → serpllm-net (8 occurrences)
Traefik labels:   serp_llm.* → serpllm.* (4 occurrences)
image:            serp_llm/invisible-playwright → serpllm/invisible-playwright
```

- [ ] **Step 6: Update docker-compose.invisible-playwright.yml**

```
image:  serp_llm/invisible-playwright → serpllm/invisible-playwright
```

### Task 3: Documentation

**Files:** `README.md`, `AGENTS.md`, `docs-src/mkdocs.yml`, `docs-src/docs/index.md`, `docs-src/docs/**/*.md` (9 pages), `PRD*.md` (5 files)

**Domain:** Text content. Bulk-replace display names and URLs.

- [ ] **Step 1: Bulk sed docs-src and root docs**

```bash
# Project name references
sed -i '' 's/serpLLM/serpLLM/g' README.md AGENTS.md PRD*.md docs-src/mkdocs.yml
sed -i '' 's/serpLLM/serpLLM/g' docs-src/docs/**/*.md

# URL references  
sed -i '' 's|github.com/zarguell/serp_llm|github.com/zarguell/serp_llm|g' README.md AGENTS.md docs-src/mkdocs.yml docs-src/docs/**/*.md

# mkdocs.yml site_url
sed -i '' 's|zarguell.github.io/serp_llm|zarguell.github.io/serp_llm|g' docs-src/mkdocs.yml

# src/serp_llm/ path references in AGENTS.md
sed -i '' 's|src/serp_llm/|src/serp_llm/|g' AGENTS.md

# compose commands referencing serp_llm service name
sed -i '' 's|logs serp_llm|logs serpllm|g' AGENTS.md scripts/ensure-gateway.sh

# docker build -t serp_llm references
sed -i '' 's|-t serp_llm|-t serpllm|g' AGENTS.md
```

- [ ] **Step 2: Manual review — README.md**

The README needs a full rewrite of the title section (lines 1-20) and clone command. Update:
- Badge URLs (if referencing serp_llm)
- Title "serpLLM" → "serpLLM"
- Description
- Clone URL

### Task 4: Admin UI templates

**Files:** `src/serp_llm/templates/*.html` (9 files)

**Domain:** HTML `<title>` tags and sidebar `<h1>`. Bulk-replace with no special logic.

- [ ] **Step 1: Bulk sed all HTML templates**

```bash
find src/serp_llm/templates -name '*.html' -exec sed -i '' 's/serpLLM/serpLLM/g' {} +
```

### Task 5: Scripts + app-internal branding

**Files:** `scripts/ensure-gateway.sh`, `config.yaml`, `src/serp_llm/config.py`, `src/serp_llm/audit.py`, `src/serp_llm/injection/classifier.py`

- [ ] **Step 1: Update ensure-gateway.sh**

```bash
sed -i '' 's/serpLLM/serpLLM/g' scripts/ensure-gateway.sh
sed -i '' 's|logs serp_llm|logs serpllm|g' scripts/ensure-gateway.sh
```

- [ ] **Step 2: Update config.yaml SMTP branding**

```
from_addr: ${SMTP_FROM:-serp_llm@localhost}  →  ${SMTP_FROM:-serpllm@localhost}
subject_prefix: "[serpLLM]"                  →  "[serpLLM]"
```

- [ ] **Step 3: Update config.py default values**

Line 328: `from_addr: str = "serp_llm@localhost"` → `"serpllm@localhost"`
Line 330: `subject_prefix: str = "[serpLLM]"` → `"[serpLLM]"`

- [ ] **Step 4: Update audit.py logger name**

`_LOGGER_NAME = "serp_llm.audit"` → `"serp_llm.audit"` (already updated by bulk sed in Task 1, verify)

- [ ] **Step 5: Update injection/classifier.py pip hint**

The classifier.py mentions `pip install 'serp_llm[injection]'` which should be `serp-llm[injection]` (note: hyphen, not underscore — pip package name)

### Task 6: Verification

- [ ] **Step 1: Ruff check**

```bash
source .venv/bin/activate && ruff check src/serp_llm/ tests/
```

Expected: Zero errors.

- [ ] **Step 2: Unit tests**

```bash
source .venv/bin/activate && pytest tests/unit/ -v
```

Expected: All tests pass. If any fail, investigate — they're likely missed import paths.

- [ ] **Step 3: Quick sanity — grep for remaining "serp_llm"**

```bash
rg -i "serp_llm" --type-add 'all:*' -l 2>/dev/null || echo "None found — clean!"
```

Expected: Zero or only historical `docs/superpowers/` files.

### Task 7: Commit and publish

- [ ] **Step 1: Git status review**

```bash
git diff --stat
```

Verify no unintended changes. Expected: ~130 files changed.

- [ ] **Step 2: Commit**

```bash
git add -A
git commit -m "rebrand: serp_llm → serp_llm

Rename the project from 'serp_llm' to 'serpLLM' (Search Engine Results
Page Orchestrator for LLMs).

- Python package: src/serp_llm/ → src/serp_llm/
- Docker networks, volumes, containers: serpllm-* → serpllm-*
- All imports, docstrings, display names updated
- Docs, README, PRDs, AGENTS.md updated
- Admin UI templates updated
- SMTP config, audit logger updated"
```

- [ ] **Step 3: Push**

```bash
git push origin rebrand/serpllm
```

### Task 8: GitHub repo rename (manual, after merge)

- [ ] **Step 1: Go to https://github.com/zarguell/serp_llm/settings**
- [ ] **Step 2: Rename to `serp_llm`**
- [ ] **Step 3: Delete old GHCR packages at `ghcr.io/zarguell/serp_llm:*`**
- [ ] **Step 4: Push a new tag to trigger fresh GHCR build**

```bash
git tag v0.2.0 && git push --tags
```

- [ ] **Step 5: Redeploy docs (GitHub Pages — trigger workflow or push to main)**
