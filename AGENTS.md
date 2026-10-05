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
- `src/espn_mcp/client.py` — async `ESPNClient` (pooling, retries, path encoding, league aliases). `errors.py` — typed errors and redaction. `middleware.py` — parent and child middleware. `config.py` — Pydantic settings (cache TTLs, profile, ports).
- `scripts/check_tool_contract.py` — source of truth for the expected tool set and annotations. Do not hard-code tool counts elsewhere.
- `scripts/check_openapi_drift.py`, `scripts/check_conformance.sh` + `conformance-baseline.yml`, `scripts/determine_bump.py`.
- `tests/` — offline unit, layered-composition, and protocol tests; `test_e2e_live.py` is opt-in (`-m e2e`).
- `.github/workflows/` — `ci.yml` (lint, py3.10–3.13 tests, contract/drift/protocol/conformance, build, CodeQL), `release.yml` (wheels, sdist, SBOM, GHCR Docker image), `drift-monitor.yml`, `dependabot-automerge.yml`.
- `server.json` (MCP Registry metadata), `Dockerfile`, `fastmcp.json`, `pyproject.toml` (entrypoint `espn-mcp`).

---

## ⚡ The Canonical Workflow: Adding a Tool for an Endpoint

1. **Client Method (`src/espn_mcp/client.py`)**:
   - Add a strictly-typed `async def` method on `ESPNClient`.
   - Apply league/sport alias normalization (e.g. `nfl` $\rightarrow$ `sport="football", league="nfl"`).
   - URL path parameters must be sanitized with `quote(str(seg), safe="")`.
2. **Tool Handler (`src/espn_mcp/tools/{games,teams,news}.py`)**:
   - Register on the domain sub-server with `@<domain>_server.tool(...)` and `@espn_tool`. `server.py` mounts each sub-server with its namespace.
   - Document arguments with clear docstrings and default options.
3. **Annotations & Gating**:
   - All ESPN tools are read-only (`readOnlyHint=True`, `destructiveHint=False`, `idempotentHint=True`, `openWorldHint=True`).
4. **Pure Offline Testing**:
   - Add unit tests in `tests/test_server.py` and `tests/test_client.py` using `httpx.MockTransport`.
   - Zero live network calls during tests. Maintain 100% statement coverage.
   - Update expected tool count in `scripts/check_tool_contract.py`.

---

## 🛡️ Non-Negotiable Safety & Protocol Rules

1. **Dynamic User-Agent**:
   - Client headers must dynamically resolve version: `"User-Agent": f"mcp-server-espn/{__version__}"`.
2. **Secret Redaction**:
   - All errors and logs pass through regex redaction (`_redact_secrets`).
3. **Multi-Stage Non-Root Containers**:
   - `Dockerfile` runs as non-root `USER mcp` with virtual environment `/opt/venv` and `ENTRYPOINT ["espn-mcp"]`.
4. **Registry Metadata Constraint**:
   - In `server.json`, root `description` must be $\le$ 100 characters.
5. **Git Safety**:
   - Never commit secrets. Never develop or push directly to `main`.

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

# Local pre-commit CodeRabbit CLI review
coderabbit review --agent --uncommitted
```

Do not create tags or releases unless the maintainer asks.
