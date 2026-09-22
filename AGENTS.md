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

This is `mcp-server-espn` — an enterprise Model Context Protocol (MCP) server exposing 15 tools providing real-time scores, play-by-play data, rosters, player statistics, betting odds, and prediction market resolution data from ESPN's public APIs. Built on FastMCP 4 Server Composition, it supports stdio and modern Streamable HTTP transports.

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

## 🏗️ Repository Layout & Architecture

```text
mcp-server-espn/
├── src/espn_mcp/
│   ├── __init__.py           # Package entrypoint and version metadata
│   ├── client.py             # Async HTTP client with connection pooling, retries, path encoding
│   ├── config.py             # Pydantic v2 Settings (SEP-2549 cache TTLs, profile, ports)
│   ├── errors.py             # Typed ESPN error hierarchy and regex credential redaction
│   ├── middleware.py         # Hierarchical parent & child middleware
│   ├── server.py             # Root FastMCP server, composition mounting, resources, prompts
│   └── tools/                # Modular domain sub-servers
│       ├── __init__.py       # Re-exports domain sub-servers and tool functions
│       ├── games.py          # espn-games sub-server (scores, summaries, schedules, standings, rankings, transactions)
│       ├── teams.py          # espn-teams sub-server (rosters, depth charts, player stats, athlete info, search, team details)
│       └── news.py           # espn-news sub-server (league news, reference resources)
├── scripts/
│   ├── check_tool_contract.py    # Contract verification asserting 15 tools and annotations
│   ├── check_openapi_drift.py    # AST visitor validating client methods against OpenAPI spec
│   ├── check_conformance.sh      # Official @modelcontextprotocol/conformance runner
│   └── determine_bump.py         # Conventional commit SemVer bump calculation script
├── tests/
│   ├── conftest.py               # Mock HTTP transport fixtures
│   ├── test_client.py            # Client route and CDN error handling tests
│   ├── test_server.py            # Tool registration, arguments, and execution tests
│   ├── test_layered.py           # FastMCP 4 composition, profiles, middleware, domain guard tests
│   ├── test_errors.py            # Structured exception and redaction tests
│   ├── test_drift.py             # AST drift verification tests
│   ├── test_protocol.py          # FastMCP Client in-memory, stdio & stateless HTTP verification
│   ├── test_determine_bump.py    # SemVer calculation tests
│   └── test_e2e_live.py          # On-demand live trial verification (-m e2e)
├── .github/workflows/
│   ├── ci.yml                    # CI matrix: lint, py3.10-3.13 tests, conformance, CodeQL, docker
│   └── release.yml               # Automated release on v* tags: wheels, sdist, CycloneDX SBOM, GHCR
├── Dockerfile                    # Multi-stage container running as non-root USER mcp
├── conformance-baseline.yml      # Expected failures baseline for protocol conformance suite
├── fastmcp.json                  # FastMCP 4 server configuration manifest
├── server.json                   # MCP Registry catalog metadata (runtimeHint: uvx, stdio transport)
├── pyproject.toml                # Packaging metadata, entrypoint CLI (espn-mcp), fastmcp>=4.0.0
└── README.md                     # User documentation and setup guide
```

---

## ⚡ The Canonical Workflow: Adding a Tool for an Endpoint

1. **Client Method (`src/espn_mcp/client.py`)**:
   - Add a strictly-typed `async def` method on `ESPNClient`.
   - Apply league/sport alias normalization (e.g. `nfl` $\rightarrow$ `sport="football", league="nfl"`).
   - URL path parameters must be sanitized with `quote(str(seg), safe="")`.
2. **Tool Handler (`src/espn_mcp/server.py`)**:
   - Decorate with `@mcp.tool()` and `@espn_tool`.
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
uv sync --extra dev

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

For release automation and packaging, push matching `v*` tags aligned with `pyproject.toml`'s `project.version` to trigger `.github/workflows/release.yml`.
