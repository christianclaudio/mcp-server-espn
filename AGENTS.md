---
vcs:
  system: github
  owner: christianclaudio
  repo: mcp-server-espn
  default_branch: main
  branch_policy: pr_only
---

# AGENTS.md

Instructions for AI coding agents (Antigravity, Claude Code, Copilot, Cursor, Windsurf) working on this repository.

---

## 🎯 Project Overview

This is `mcp-server-espn` — an enterprise Model Context Protocol (MCP) server providing real-time scores, play-by-play data, rosters, player statistics, betting odds, and prediction market resolution data from ESPN's public APIs. Built on FastMCP 4 Server Composition, it supports stdio and modern Streamable HTTP transports. The expected tool set lives in `scripts/check_tool_contract.py`.

---

## 📚 Canonical Documentation & Live Doc MCPs

Before designing, implementing, or updating any MCP tool, always consult the official machine-readable documentation indexes ("the bibles") and live documentation MCP servers:

### Machine-Readable Documentation Indexes (`llms.txt`)
| Resource | URL | Focus Areas |
| :--- | :--- | :--- |
| **FastMCP 4 Framework** | [`https://gofastmcp.com/llms.txt`](https://gofastmcp.com/llms.txt) | Server composition (`mount`), hierarchical middleware, transforms (`ToolTransform`, `ToolSearch`), lifespans, in-memory testing |
| **Model Context Protocol (Official)** | [`https://modelcontextprotocol.io/llms.txt`](https://modelcontextprotocol.io/llms.txt) | Wire protocol spec (Spec 2026-07-28), Streamable HTTP framing, tool annotations, elicitation, catalog caching |

### Live Documentation MCP Servers
Both ecosystems publish live, queryable Documentation MCP servers exposing full search and doc navigation tools:

1. **FastMCP Documentation Server**:
   - **Endpoint**: `https://gofastmcp.com/mcp` (SSE / Streamable HTTP)
   - **Tools**: `search_fast_mcp(query)`, `query_docs_filesystem_fast_mcp(command)`, `submit_feedback(...)`
2. **Anthropic Model Context Protocol Server**:
   - **Endpoint**: `https://modelcontextprotocol.io/mcp` (SSE / Streamable HTTP)
   - **Tools**: `search_model_context_protocol(query)`, `query_docs_filesystem_model_context_protocol(command)`, `submit_feedback(...)`

---

## 🏗️ Key Paths

- `src/espn_mcp/server.py` — root FastMCP server: mounts the `games`, `teams`, and `news` sub-servers with matching namespaces; resources, prompts.
- `src/espn_mcp/tools/{games,teams,news}.py` — domain sub-servers holding every tool; re-exported from `tools/__init__.py`.
- `src/espn_mcp/profiles.py` — `PROFILES` registry: domain-mount profiles (`full`, `games`, `teams`, `news`, `readonly`) and job allowlist profiles (`gameday`, `betting`, `scouting`, `season`), each with a one-line job and exact tool names; `is_read_only_tool`, `ReadOnlyToolFilter`, `FULL_ONLY_TOOLS`.
- `src/espn_mcp/client.py` — async `ESPNClient` (pooling, retries, path encoding, league aliases). `errors.py` — typed errors, redaction and the shared `espn_tool` decorator (`SafetyViolationError` is a FastMCP `ToolError`). `middleware.py` — parent middleware (audit, `ReadOnlyGateMiddleware` driven only by `readOnlyHint`) and child domain guards. `config.py` — Pydantic settings (cache TTLs, profile, read-only, Tool Search backend, Code Mode, ports).
- `scripts/check_tool_contract.py` — source of truth for the expected tool set, annotations, and per-profile `(listed, read-only)` counts (`EXPECTED_PROFILE_COUNTS`). Do not hard-code tool counts elsewhere.
- `scripts/check_openapi_drift.py`, `scripts/check_conformance.sh` + `conformance-baseline.yml`.
- `scripts/release_notes.py` — release body from squash commits since the previous `v*` tag. `scripts/check_version.py` — runs after `uv build` and reads the version from the single wheel in `dist/` (the file that ships, as release.yml's tag check does); fails on `0.0.0` (no git metadata) or `0.0.1.devN` (no reachable tag, a shallow checkout).
- `tests/` — offline unit, layered-composition, and protocol tests; `test_e2e_live.py` is opt-in (`uv run pytest -m e2e --no-cov`).
- `.github/workflows/` — `ci.yml` (lint, py3.10–3.13 tests, contract/drift/protocol/conformance, build, CodeQL), `release.yml` (on a `v*` tag: build with full history, check the wheel version matches the tag, build the release notes, then SBOM, build provenance, PyPI, the GitHub Release from `scripts/release_notes.py` with the wheel, sdist and SBOM, the GHCR Docker image, and MCP Registry publish last (the tag version is stamped into `server.json`); only the provenance step is `continue-on-error`), `drift-monitor.yml`, `dependabot-automerge.yml` (squash auto-merge only for Dependabot PRs whose highest update is minor or patch; major updates wait for a human review).
- `server.json` (MCP Registry metadata), `Dockerfile`, `fastmcp.json`, `pyproject.toml` (console scripts `mcp-server-espn`, which must equal the `server.json` identifier, and `espn-mcp`, which the README configs and Dockerfile run).

---

## ⚡ The Canonical Workflow: Adding a Tool for an Endpoint

1. **Client Method (`src/espn_mcp/client.py`)**:
   - Add a strictly-typed `async def` method on `ESPNClient`.
   - Apply league/sport alias normalization (e.g. `nfl` $\rightarrow$ `sport="football", league="nfl"`).
   - URL path parameters must be sanitized with `quote(str(seg), safe="")`.
2. **Tool Handler (`src/espn_mcp/tools/{games,teams,news}.py`)**:
   - Register on the domain sub-server with `@<domain>_server.tool(...)` and `@espn_tool` (from `espn_mcp.errors`). `server.py` mounts each sub-server with its namespace.
   - `@espn_tool` wraps success as `{"status": "success", "data": ...}` and re-raises any failure as a FastMCP `ToolError` with the redacted message, so the client gets `isError: true`. Never return an error payload from a tool.
   - Document arguments with clear docstrings and default options.
3. **Annotations & Gating**:
   - All ESPN tools are read-only (`readOnlyHint=True`, `destructiveHint=False`, `idempotentHint=True`, `openWorldHint=True`).
   - `readOnlyHint` is the only read-only signal: under `ESPN_MCP_READONLY=1` or the `readonly` profile a tool without `readOnlyHint=True` is hidden and refused. Never gate on tool-name prefixes.
   - Place the new tool in at least one job profile in `src/espn_mcp/profiles.py` (or add it to `FULL_ONLY_TOOLS`), then update `EXPECTED_PROFILE_COUNTS`.
4. **Pure Offline Testing**:
   - Add unit tests in `tests/test_server.py` and `tests/test_client.py` using `httpx.MockTransport`.
   - Zero live network calls during tests. Maintain 100% statement coverage.
   - Update the expected tool set and profile counts in `scripts/check_tool_contract.py`.

---

## 🛡️ Non-Negotiable Safety & Protocol Rules

1. **Dynamic User-Agent**:
   - Client headers must dynamically resolve version: `"User-Agent": f"mcp-server-espn/{__version__}"`.
2. **Secret Redaction**:
   - All errors and logs pass through regex redaction (`redact_secrets` in `errors.py`).
3. **Multi-Stage Non-Root Containers**:
   - `Dockerfile` runs as non-root `USER mcp` with virtual environment `/opt/venv` and `ENTRYPOINT ["espn-mcp"]`.
4. **Registry Metadata Constraint**:
   - In `server.json`, root `description` must be $\le$ 100 characters.
5. **Git Safety & Releases**:
   - Never commit secrets. Never develop or push directly to `main`.
   - **The git tag is the version.** `uv-dynamic-versioning` reads the `vX.Y.Z` tag at build time; `pyproject.toml` declares `dynamic = ["version"]`, `__version__` comes from `importlib.metadata`, and `server.json` commits `0.0.0` (the release workflow stamps the tag version into it). PRs never edit a version: no bump in `pyproject.toml`, `src/espn_mcp/__init__.py`, `server.json`, `uv.lock`, or `CHANGELOG.md`. Untagged builds report `X.Y.(Z+1).devN+<sha>`; a build with no git metadata reports the fallback `0.0.0`, which `scripts/check_version.py` rejects when run on the built wheel after `uv build`.
   - **Breaking changes:** every `feat!` / `fix!` PR (any `type!:` title) carries a `BREAKING CHANGE:` footer as the final paragraph of the PR body, and the footer text must include the migration steps. `BREAKING CHANGE:` (or its synonym `BREAKING-CHANGE:`) is the only footer token; do not add a separate migration token. `scripts/release_notes.py` stops at the CodeRabbit marker line (outside a code fence) `<!-- This is an auto-generated comment: release notes by coderabbit.ai -->` and ignores everything after it, so the footer goes before CodeRabbit's generated summary, never inside it.
   - **Squash merges use the PR body as the commit message** (repo settings: PR title as squash title, PR body as squash message). Keep the PR body accurate up to the merge, because `scripts/release_notes.py` reads it from the squash commit.
   - **`CHANGELOG.md` is frozen** as of 1.2.9. GitHub Releases are the changelog: `scripts/release_notes.py` builds each release body from the squash commits since the previous tag (every `BREAKING CHANGE:` footer verbatim, then the commit subjects). Do not add CHANGELOG entries.
   - `skills/espn-mcp/SKILL.md` carries no version: the [Agent Skills specification](https://agentskills.io/specification) has no top-level `version` field. Do not add one. Update the skill only when its operator guidance changes.
   - **README is outside the release version ceremony.** Do not add or chase `README.md` `==X.Y.Z` install pins. Update `README.md` only when project behavior, install method, config, or commands actually change. Prefer unpinned install examples (`uvx --from mcp-server-espn espn-mcp`) or point readers to GitHub Releases.

---

## 🛠️ Development & Verification Commands

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

# Upstream OpenAPI / route drift check
uv run python scripts/check_openapi_drift.py

# Protocol integration tests (stdio handshake & stateless streamable HTTP)
uv run pytest tests/test_protocol.py

# Build version guard (reads the single wheel in dist/; rejects 0.0.0 and the untagged 0.0.1.devN)
rm -rf dist && uv build && uv run python scripts/check_version.py

# Local pre-commit CodeRabbit CLI review
coderabbit review --agent --uncommitted
```

---

## 🔄 CI & Releases

CI is defined in `.github/workflows/ci.yml` (jobs: lint and types, tests on Python 3.10–3.13 at 100% coverage, tool contract + OpenAPI drift + protocol + conformance, build + `scripts/check_version.py` + `twine check`, CodeQL). Jobs that install or build the package, and `drift-monitor.yml`, check out with `fetch-depth: 0`, because a shallow checkout has no reachable tag and reports `0.0.1.devN`. The Docker image is built only in `release.yml`, with `UV_DYNAMIC_VERSIONING_BYPASS` set to the tag version (the build context has no `.git`). Run the commands above before opening a PR. Scheduled upstream drift runs in `drift-monitor.yml`.

Do not create tags or releases unless the maintainer asks. There is no release PR: merged commits accumulate on `main`, and releases go out on any weekday on the maintainer's go; no fixed release day. Before the tag:

- The release owner previews the release body on an up-to-date `main` with full history and tags (`git fetch --tags && python3 scripts/release_notes.py`) and posts it with the release Ask.
- The reviewer checks the proposed version against the commit types since the last tag (`!` / `BREAKING CHANGE:` → major, `feat` → minor, otherwise patch), that every breaking commit carries its footer with migration steps, and that the version is unused in all three places it could already exist:
  ```bash
  git ls-remote --tags origin vX.Y.Z                                                    # prints nothing
  curl -s -o /dev/null -w '%{http_code}\n' https://pypi.org/pypi/mcp-server-espn/X.Y.Z/json   # prints 404
  curl -s -o /dev/null -w '%{http_code}\n' https://registry.modelcontextprotocol.io/v0.1/servers/io.github.christianclaudio%2Fespn/versions/X.Y.Z   # prints 404
  ```
  In a throwaway clone, the reviewer tags the release commit locally, runs `rm -rf dist && uv build`, and confirms the wheel is `mcp_server_espn-X.Y.Z-py3-none-any.whl` and `scripts/check_version.py` passes; then discards the clone without pushing.
- Only the maintainer's go creates the tag. PyPI never accepts the same version twice: if a release fails after the PyPI upload, do not re-run it; merge a fix and tag the next patch.
