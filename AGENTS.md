# AGENTS.md

Instructions for AI coding agents (Antigravity, Claude Code, Copilot, Cursor, Windsurf) working on this repository.

---

## 🎯 Project Overview

This is `mcp-server-espn` — an enterprise Model Context Protocol (MCP) server exposing 10 tools providing real-time scores, play-by-play data, rosters, player statistics, betting odds, and prediction market resolution data from ESPN's public APIs. It runs over stdio and is consumed by AI clients (Claude Desktop, VS Code, Antigravity, Cursor, etc.).

---

## 🏗️ Architecture Blueprint

```
mcp-server-espn/
├── src/espn_mcp/
│   ├── __init__.py       # Package version (__version__) and public exports
│   ├── server.py         # MCPServer instance, @mcp.tool() registrations, prompts, resources
│   ├── client.py         # Async HTTP client (httpx.AsyncClient, CDN headers, retries, jitter)
│   ├── errors.py         # Structured ESPN API exceptions and automatic secret redaction
│   └── config.py         # Pydantic Settings and environment variable resolution
├── scripts/
│   ├── check_tool_contract.py    # Contract verification asserting 10 tools and annotations
│   └── check_openapi_drift.py    # AST visitor validating client methods against OpenAPI spec
├── tests/
│   ├── conftest.py               # Mock HTTP transport fixtures
│   ├── test_client.py            # Client route and CDN error handling tests
│   ├── test_server.py            # Tool registration, arguments, and execution tests
│   ├── test_errors.py            # Structured exception and redaction tests
│   ├── test_drift.py             # AST drift verification tests
│   ├── test_protocol.py          # Wire-level stdio & stateless streamable HTTP protocol verification
│   └── test_e2e_live.py          # On-demand live trial verification (-m e2e)
├── .github/workflows/
│   ├── ci.yml                    # CI matrix: lint, py3.10-3.13 tests, contracts, CodeQL, docker build
│   └── release.yml               # Automated release on v* tags: wheels, sdist, CycloneDX SBOM, GHCR
├── Dockerfile                    # Multi-stage container running as non-root USER mcp
├── server.json                   # MCP Registry catalog metadata (runtimeHint: uvx, stdio transport)
├── pyproject.toml                # Packaging metadata, entrypoint CLI (espn-mcp), mcp>=2.1.1
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
