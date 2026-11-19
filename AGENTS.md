# AGENTS.md

Instructions for AI coding agents working on `mcp-server-espn`.

---

## 🎯 Project Overview

`mcp-server-espn` is an enterprise Model Context Protocol (MCP) server exposing real-time scores, play-by-play data, rosters, player statistics, betting odds, and prediction market resolution data from ESPN's public APIs. Built on FastMCP 4 Server Composition over stdio and Streamable HTTP (`scripts/check_tool_contract.py` defines expected tools).

---

## 📚 Canonical Documentation & Live Doc MCPs

Always consult the official machine-readable indexes and live documentation MCP servers before designing or changing tools:

| Resource | `llms.txt` index | Focus |
| :--- | :--- | :--- |
| **FastMCP 4 Framework** | [`https://gofastmcp.com/llms.txt`](https://gofastmcp.com/llms.txt) | `mount`, hierarchical middleware, `ToolTransform`, `ToolSearch`, lifespans, in-memory testing |
| **Model Context Protocol (Official)** | [`https://modelcontextprotocol.io/llms.txt`](https://modelcontextprotocol.io/llms.txt) | Spec 2026-07-28, Streamable HTTP framing, annotations, elicitation, catalog caching |

Live doc MCP endpoints: `https://gofastmcp.com/mcp` (`search_fast_mcp`, `query_docs_filesystem_fast_mcp`) and `https://modelcontextprotocol.io/mcp` (`search_model_context_protocol`, `query_docs_filesystem_model_context_protocol`).

---

## 🏗️ Key Paths

- `src/espn_mcp/server.py` — root FastMCP server mounting `games`, `teams`, and `news` sub-servers with matching namespaces; resources, prompts.
- `src/espn_mcp/tools/{games,teams,news}.py` — domain sub-servers holding all tools; re-exported from `tools/__init__.py`.
- `src/espn_mcp/client.py` — async `ESPNClient` (pooling, retries, path encoding, league aliases).
- `src/espn_mcp/{auth,errors,middleware,config}.py` — `ESPN_MCP_AUTH_TOKEN` bearer verifier + tokenless non-localhost HTTP bind refusal, typed errors & redaction, parent/child middleware, Pydantic settings (cache TTLs, profile, ports).
- `scripts/check_tool_contract.py` — source of truth for expected tools and annotations (never hard-code counts elsewhere).
- `scripts/check_openapi_drift.py` (the PR path checks client calls against `DEFAULT_ESPN_SPEC`; `--fail-on-new-uncovered` diffs the core WADLs against `scripts/espn_routes_snapshot.tsv` and liveness-checks `scripts/espn_site_routes.txt`), `scripts/check_conformance.sh` + `conformance-baseline.yml`, `scripts/release_notes.py`, `scripts/check_version.py` (rejects `0.0.0` / `0.0.1.devN` from missing git metadata or shallow checkouts).
- `tests/` — offline unit, layered-composition, and protocol tests; live checks (`-m live`) require no credentials against ESPN's public API (`uv run pytest -m live --no-cov`).
- `.github/workflows/` — `ci.yml`, `release.yml`, `dependabot-automerge.yml`.
- `server.json`, `Dockerfile`, `fastmcp.json`, `pyproject.toml` (console scripts `mcp-server-espn` matching `server.json` identifier, and `espn-mcp` used by README configs and `Dockerfile`).

---

## ⚡ Canonical Workflow: Adding a Tool for an Endpoint

1. **Client method (`src/espn_mcp/client.py`):** add strictly typed `async def` method on `ESPNClient`, normalize league/sport aliases (e.g., `nfl` → `sport="football", league="nfl"`), and sanitize URL path segments with `quote(str(seg), safe="")`.
2. **Tool handler (`src/espn_mcp/tools/{games,teams,news}.py`):** register on domain sub-server with `@<domain>_server.tool(...)` and `@espn_tool` (`server.py` mounts with namespace); document arguments and defaults clearly.
3. **Annotations & gating:** all ESPN tools are read-only (`readOnlyHint=True`, `destructiveHint=False`, `idempotentHint=True`, `openWorldHint=True`).
4. **Pure offline testing (`tests/`):** add unit tests in `tests/test_server.py` and `tests/test_client.py` using `httpx.MockTransport` (**zero live calls in default suite**, **100%** statement coverage), and update `scripts/check_tool_contract.py`.

---

## 🛡️ Non-Negotiable Safety & Protocol Rules

1. **Dynamic User-Agent:** client headers must dynamically resolve version: `"User-Agent": f"mcp-server-espn/{__version__}"`.
2. **Secret Redaction:** all errors and logs pass through `_redact_secrets` / `redact_message` in `errors.py` (masking credentials, keys, tokens, PEM blocks while leaving `next_token`, `page_token`, `max_tokens` untouched; see `errors.py` for known multiline/bracket limits).
3. **Multi-Stage Non-Root Containers (`Dockerfile`):**
   - Multi-stage `python:3.12-slim-trixie AS builder` → runtime pinned by digest (both stages share the same pin; glibc is keyed by the Debian release in the tag, not the digest), non-root `USER mcp`, venv `/opt/venv`, `ENTRYPOINT ["espn-mcp"]`.
   - Trust-store floor (FastMCP Oct 2026 / `DSA-6572-1`): both stages upgrade OS `ca-certificates` and enforce `ARG CA_CERTIFICATES_FLOOR` (before any pip runs in builder), builder bootstraps pip from `docker/pip-bootstrap.txt` (`--require-hashes`; Dependabot bumps it), and `certifi>=2026.10.23` is a direct dependency. Never lower `CA_CERTIFICATES_FLOOR` via `--build-arg` (the build-arg exists only so CI can raise it for the negative floor check; CI and the release workflow never pass it otherwise).
   - Reproducible lock install: builder installs uv (`docker/uv-bootstrap.txt`, `--require-hashes`, outside venv; Dependabot bumps it), exports `uv export --locked --no-dev --no-emit-project --format requirements.txt` and installs that file with pip in hash-checking mode, then installs `docker/build-backend.txt` (`--require-hashes --only-binary=:all:` outside venv), and runs `PYTHONPATH=/tmp/build-backend pip install --no-deps --no-build-isolation .` (never `pip install .`). Regenerate `docker/build-backend.txt` after `uv lock` via `uv run python scripts/gen_build_backend.py` (never hand-edit; Dependabot excludes it). Enforced by `tests/test_dockerfile.py` & `tests/test_build_backend_lock.py`.
4. **Registry Metadata (`server.json`):** root `description` **≤ 100 chars**.
5. **Git Safety & Releases:**
   - Never commit secrets or push to `main`; use feature branches and PRs.
   - **Tag is version (`uv-dynamic-versioning`):** `pyproject.toml` declares `dynamic = ["version"]`, `__version__` uses `importlib.metadata`, `server.json` commits `0.0.0`. Never bump versions in PRs (`pyproject.toml`, `src/espn_mcp/__init__.py`, `server.json`, `uv.lock`, `CHANGELOG.md`). Untagged builds report `X.Y.(Z+1).devN+<sha>`; `scripts/check_version.py` rejects `0.0.0` / `0.0.1.devN`.
   - **Breaking changes (`feat!` / `fix!`):** end PR body with `BREAKING CHANGE:` as the final paragraph (before `<!-- This is an auto-generated comment: release notes by coderabbit.ai -->` if present) and **must include migration steps** (no separate migration token; `scripts/release_notes.py` stops at the CodeRabbit marker). Keep PR body accurate before squash merge.
   - **`CHANGELOG.md` frozen at 1.2.9:** GitHub Releases are the changelog. Leave `README.md` unpinned (`uvx --from mcp-server-espn espn-mcp`).
   - **Version level:** before tagging, check the proposed version against the commit types since the last tag (`!` / `BREAKING CHANGE:` → major, `feat` → minor, otherwise patch), and that every breaking commit carries its footer with migration steps.
   - **Tagging (maintainer go only, no release PR):** preview `git fetch --tags && python3 scripts/release_notes.py` on `main` and post it with the release Ask; verify version unused on git tags, PyPI (`https://pypi.org/pypi/mcp-server-espn/X.Y.Z/json`), and MCP Registry (`https://registry.modelcontextprotocol.io/v0.1/servers/io.github.christianclaudio%2Fespn/versions/X.Y.Z`). In a throwaway clone, tag the release commit locally, run `rm -rf dist && uv build`, and confirm the wheel is `mcp_server_espn-X.Y.Z-py3-none-any.whl` and `scripts/check_version.py` passes; discard the clone without pushing. PyPI rejects re-uploads: on post-PyPI failure, merge a fix and tag next patch.

---

## 🛠️ Development & Verification Commands

Requires `uv >= 0.13.4` (`[tool.uv] required-version` in `pyproject.toml`).

```bash
# Install editable with dev dependencies
uv sync --locked --extra dev

# Lint and formatting
uv run ruff check . && uv run ruff format --check .

# Strict type checking
uv run mypy --strict src/

# Test suite with 100% coverage requirement
uv run pytest --cov=src/espn_mcp --cov-fail-under=100 -v

# Tool contract verification
uv run python scripts/check_tool_contract.py

# Upstream OpenAPI / route drift check (PR path: client calls vs the committed spec)
uv run python scripts/check_openapi_drift.py

# Scheduled drift gate (new uncovered core routes, and dead site samples)
uv run python scripts/check_openapi_drift.py --fail-on-new-uncovered

# Protocol integration tests (stdio handshake & stateless streamable HTTP)
uv run pytest tests/test_protocol.py

# Build version guard (reads the single wheel in dist/; rejects 0.0.0 and the untagged 0.0.1.devN)
rm -rf dist && uv build && uv run python scripts/check_version.py

# Local pre-commit CodeRabbit CLI review
coderabbit review --agent --uncommitted
```

---

## 🔄 CI & Releases

- **CI (`.github/workflows/ci.yml`):** runs on `push`/`pull_request` to `main`, weekly schedule, and `workflow_dispatch` (`fetch-depth: 0` on package jobs). Jobs: Lint and types, Tests (Python 3.11–3.14, 100% cov + aggregate Tests), Tool contract assertions (+ OpenAPI drift & stdio check), Protocol conformance, Build and package check (`check_version.py` + `twine check`), Container image build (no push; verifies stripped build deps, stdio `initialize`/`tools/list`, negative `CA_CERTIFICATES_FLOOR`), and Check API Drift (`drift-check`, schedule/manual only; that job alone passes `--fail-on-new-uncovered`, has `contents: read` and `issues: write`, and opens an issue when the scan reports new uncovered routes, a dead site route, or another failure). CodeQL runs via GitHub default setup.
- **Dependabot (`.github/dependabot.yml` + `dependabot-automerge.yml`):** squash-merges minor/patch PRs after CI passes on head commit; major & docker updates wait for human review.
- **Releases (`.github/workflows/release.yml`):** triggered on `v*` tags (`fetch-depth: 0`); 5 jobs: `build` (verifies wheel & SBOM), `publish` (PyPI + GitHub Release via `release_notes.py`), `docker` (pushes GHCR image with `UV_DYNAMIC_VERSIONING_BYPASS` set to the tag version because the Docker build context has no `.git`, and without buildx provenance/SBOM), `attest` (attests `dist/*` + image digest), and `registry` (stamps tag into `server.json` & publishes to MCP Registry).

---

## VCS

```yaml
vcs:
  host: github
  remote: origin
  repository: christianclaudio/mcp-server-espn
  default_branch: main
  protected_branches: [main]
  branch_policy: pr_only
  merge_strategy: squash
  delete_branch_on_merge: true
  require_coderabbit: true
  branch_naming: type/scope-desc
```
