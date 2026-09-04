# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-09-03

### Added
- **MCP 2026-07-28 Spec Compliance**: Modernized architecture for the new MCP specification.
- **Deterministic Tool Catalog Caching**: Added `CacheHint(ttl_ms=3600000, scope="public")` across `tools/list`, `prompts/list`, `resources/list`, and `server/discover` per SEP-2549.
- **Multi Round-Trip Request (MRTR) Elicitation**: Enabled interactive user confirmation on destructive operations (`delete_item`, `bulk_delete_items`) using `ctx.elicit()` with `ConfirmationResponse` schema per SEP-2322.
- **Streamable HTTP & Transport Deprecation**: Added CLI transport flags (`--transport stdio|streamable-http|sse`) and deprecation warnings for legacy HTTP+SSE per SEP-2577.

## [1.0.2] - 2026-09-02

### Added
- **Enterprise Parameter & Schema Drift Engine**: Upgraded `scripts/check_openapi_drift.py` with AST client inspection, endpoint path parity, parameter deprecation detection (`deprecated: true` / `[Deprecated]`), and missing required parameter audits.
- **Graceful OS Shutdown**: Direct kernel `os._exit(0)` signal interceptor for supervisor reload compatibility.

## [1.0.0] - 2026-08-30

### Added
- Initial enterprise MCP server template release.
- Hardened `httpx.AsyncClient` with connection pooling, path traversal encoding, and exponential backoff retry.
- FastMCP tool definitions with 100% MCP 2.0 tool annotations (`readOnlyHint`, `destructiveHint`, `idempotentHint`).
- Safety gating with single-delete `confirm=True` enforcement and `ALLOW_BULK_DESTRUCTIVE` environment switches.
- Automated regex credential scrubbing (`redact_secrets`) across all exceptions and logging outputs.
- Comprehensive test suite with 100.0% statement line coverage.
- Tool contract validator (`scripts/check_tool_contract.py`), OpenAPI drift detector (`scripts/check_openapi_drift.py`), and stdio handshake smoke test (`scripts/smoke_test.py`).
- Automated multi-channel CI/CD workflows for PyPI OIDC, MCP Registry OIDC, GHCR Docker, CycloneDX SBOM, and provenance attestations.
