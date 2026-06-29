# serpLLM Rebrand Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebrand the entire project from "webgateway" to "serp_llm" — Python package, Docker infrastructure, docs, UI, scripts, config, and CI — as one atomic change.

**Architecture:** Pure mechanical rename. One branch, one commit. `git mv src/webgateway/ src/serp_llm/` → bulk sed across all file types → domain-specific file edits → ruff fix → unit tests → commit.

**Tech Stack:** Python, Docker Compose, MkDocs, Jinja2 templates, FastAPI

**Spec:** `docs/superpowers/specs/2026-06-29-rebrand-serpllm-design.md`

---

### Task 1: Python package rename (git mv + bulk sed)

**Files:** All `.py` files under `src/`, `tests/`, plus `pyproject.toml`, `Dockerfile`

**Naming map:**
| Search | Replace | Context |
|---|---|---|
| `webgateway` (in imports/paths) | `serp_llm` | Python module paths, `@patch` strings |
| `WebGateway` (in display strings) | `serpLLM` | Docstrings, FastAPI title, MCP name |
| `webgateway-` (in Docker names) | `serpllm-` | Networks, volumes, container names, images |
| `webgateway` (in system user) | `serpllm` | Dockerfile user/group |

- [ ] **Step 1: Create branch and move package directory**

```bash
git checkout -b rebrand/serpllm
git mv src/webgateway src/serp_llm
```

- [ ] **Step 2: Bulk sed across all Python files — module paths**

Replace `webgateway` → `serp_llm` but ONLY in import/path contexts. This covers:
- `from webgateway.xxx import YYY` → `from serp_llm.xxx import YYY`
- `import webgateway.xxx` → `import serp_llm.xxx`
- `@patch("webgateway.xxx.yyy")` → `@patch("serp_llm.xxx.yyy")`
- `webgateway.main:app` → `serp_llm.main:app`

```bash
# Python imports and module paths
find src/serp_llm tests -name '*.py' -exec sed -i '' 's/\bwebgateway\b/serp_llm/g' {} +
```

- [ ] **Step 3: Bulk sed — display names**

Replace `WebGateway` → `serpLLM` in display/title contexts (docstrings, FastAPI title, MCP server name, HTML titles)

```bash
find src/serp_llm tests -name '*.py' -exec sed -i '' 's/WebGateway/serpLLM/g' {} +
```

- [ ] **Step 4: Update pyproject.toml**

```
name = "serp-llm"                    (was: webgateway)
packages = ["src/serp_llm"]          (was: src/webgateway)
known-first-party = ["serp_llm"]     (was: webgateway)
```

- [ ] **Step 5: Update Dockerfile**

Replace: user/group `webgateway` → `serpllm`, CMD module path `serp_llm.main:app`
Also update docker-compose files (see Task 2)

- [ ] **Step 6: Fix the `docker build` reference in AGENTS.md**

Line 135: `docker build --build-arg ENABLE_INJECTION=1 -t webgateway .` → `-t serpllm .`

- [ ] **Step 7: Run ruff to fix any import ordering**

```bash
source .venv/bin/activate && ruff check --fix src/serp_llm/ tests/
```

### Task 2: Docker infrastructure

**Files:** `Dockerfile`, `docker-compose.yml`, `docker-compose.test.yml`, `docker-compose.local.yml`, `docker-compose.selfhosted.yml`, `docker-compose.invisible-playwright.yml`

**Domain:** Each compose file gets unique replacements. No conflicts with Task 1 since compose files aren't Python.

- [ ] **Step 1: Update Dockerfile non-Python branding**

```
groupadd:  webgateway → serpllm
useradd:   webgateway → serpllm
USER:      webgateway → serpllm
chown:     webgateway → serpllm (8 occurrences)
```

- [ ] **Step 2: Update docker-compose.yml**

```
service name:  webgateway → serpllm
network:       webgateway-net → serpllm-net (5 occurrences)
image:         webgateway/invisible-playwright → serpllm/invisible-playwright
```

- [ ] **Step 3: Update docker-compose.test.yml**

```
service name:  webgateway → serpllm
network:       webgateway-net → serpllm-net (2 occurrences)
```

- [ ] **Step 4: Update docker-compose.local.yml**

```
service name:  webgateway → serpllm
volumes:       webgateway-data → serpllm-data, webgateway-logs → serpllm-logs
```

- [ ] **Step 5: Update docker-compose.selfhosted.yml**

```
container names:  webgateway-* → serpllm-* (6 occurrences)
network:          webgateway-net → serpllm-net (8 occurrences)
Traefik labels:   webgateway.* → serpllm.* (4 occurrences)
image:            webgateway/invisible-playwright → serpllm/invisible-playwright
```

- [ ] **Step 6: Update docker-compose.invisible-playwright.yml**

```
image:  webgateway/invisible-playwright → serpllm/invisible-playwright
```

### Task 3: Documentation

**Files:** `README.md`, `AGENTS.md`, `docs-src/mkdocs.yml`, `docs-src/docs/index.md`, `docs-src/docs/**/*.md` (9 pages), `PRD*.md` (5 files)

**Domain:** Text content. Bulk-replace display names and URLs.

- [ ] **Step 1: Bulk sed docs-src and root docs**

```bash
# Project name references
sed -i '' 's/WebGateway/serpLLM/g' README.md AGENTS.md PRD*.md docs-src/mkdocs.yml
sed -i '' 's/WebGateway/serpLLM/g' docs-src/docs/**/*.md

# URL references  
sed -i '' 's|github.com/zarguell/webgateway|github.com/zarguell/serp_llm|g' README.md AGENTS.md docs-src/mkdocs.yml docs-src/docs/**/*.md

# mkdocs.yml site_url
sed -i '' 's|zarguell.github.io/webgateway|zarguell.github.io/serp_llm|g' docs-src/mkdocs.yml

# src/webgateway/ path references in AGENTS.md
sed -i '' 's|src/webgateway/|src/serp_llm/|g' AGENTS.md

# compose commands referencing webgateway service name
sed -i '' 's|logs webgateway|logs serpllm|g' AGENTS.md scripts/ensure-gateway.sh

# docker build -t webgateway references
sed -i '' 's|-t webgateway|-t serpllm|g' AGENTS.md
```

- [ ] **Step 2: Manual review — README.md**

The README needs a full rewrite of the title section (lines 1-20) and clone command. Update:
- Badge URLs (if referencing webgateway)
- Title "WebGateway" → "serpLLM"
- Description
- Clone URL

### Task 4: Admin UI templates

**Files:** `src/serp_llm/templates/*.html` (9 files)

**Domain:** HTML `<title>` tags and sidebar `<h1>`. Bulk-replace with no special logic.

- [ ] **Step 1: Bulk sed all HTML templates**

```bash
find src/serp_llm/templates -name '*.html' -exec sed -i '' 's/WebGateway/serpLLM/g' {} +
```

### Task 5: Scripts + app-internal branding

**Files:** `scripts/ensure-gateway.sh`, `config.yaml`, `src/serp_llm/config.py`, `src/serp_llm/audit.py`, `src/serp_llm/injection/classifier.py`

- [ ] **Step 1: Update ensure-gateway.sh**

```bash
sed -i '' 's/WebGateway/serpLLM/g' scripts/ensure-gateway.sh
sed -i '' 's|logs webgateway|logs serpllm|g' scripts/ensure-gateway.sh
```

- [ ] **Step 2: Update config.yaml SMTP branding**

```
from_addr: ${SMTP_FROM:-webgateway@localhost}  →  ${SMTP_FROM:-serpllm@localhost}
subject_prefix: "[WebGateway]"                  →  "[serpLLM]"
```

- [ ] **Step 3: Update config.py default values**

Line 328: `from_addr: str = "webgateway@localhost"` → `"serpllm@localhost"`
Line 330: `subject_prefix: str = "[WebGateway]"` → `"[serpLLM]"`

- [ ] **Step 4: Update audit.py logger name**

`_LOGGER_NAME = "webgateway.audit"` → `"serp_llm.audit"` (already updated by bulk sed in Task 1, verify)

- [ ] **Step 5: Update injection/classifier.py pip hint**

The classifier.py mentions `pip install 'webgateway[injection]'` which should be `serp-llm[injection]` (note: hyphen, not underscore — pip package name)

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

- [ ] **Step 3: Quick sanity — grep for remaining "webgateway"**

```bash
rg -i "webgateway" --type-add 'all:*' -l 2>/dev/null || echo "None found — clean!"
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
git commit -m "rebrand: webgateway → serp_llm

Rename the project from 'webgateway' to 'serpLLM' (Search Engine Results
Page Orchestrator for LLMs).

- Python package: src/webgateway/ → src/serp_llm/
- Docker networks, volumes, containers: webgateway-* → serpllm-*
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

- [ ] **Step 1: Go to https://github.com/zarguell/webgateway/settings**
- [ ] **Step 2: Rename to `serp_llm`**
- [ ] **Step 3: Delete old GHCR packages at `ghcr.io/zarguell/webgateway:*`**
- [ ] **Step 4: Push a new tag to trigger fresh GHCR build**

```bash
git tag v0.2.0 && git push --tags
```

- [ ] **Step 5: Redeploy docs (GitHub Pages — trigger workflow or push to main)**
