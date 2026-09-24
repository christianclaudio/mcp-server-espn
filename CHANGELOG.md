# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.2.0] - 2026-09-22

### Added
- **Expanded Tool Suite (33 Tools)**: Scaled server from 10 to 33 tools across three domain sub-servers:
  - `espn-games` (20 tools): `games_get_scoreboard`, `games_get_game_summary`, `games_get_team_schedule`, `games_get_standings`, `games_get_rankings`, `games_get_transactions`, `games_get_leaders_by_athlete`, `games_get_leaders_by_team`, `games_get_league_groups`, `games_get_league_events`, `games_get_league_draft`, `games_get_scoreboard_header`, `games_get_event_odds`, `games_get_play_by_play`, `games_get_game_situation`, `games_get_win_probabilities`, `games_get_game_predictor`, `games_get_calendar`, `games_get_futures`, and `games_get_power_index`.
  - `espn-teams` (12 tools): `teams_search`, `teams_list_teams`, `teams_get_team`, `teams_get_team_statistics`, `teams_get_team_roster`, `teams_get_team_depth_chart`, `teams_get_player_stats`, `teams_get_athlete_overview`, `teams_get_athlete_bio`, `teams_get_athlete_stats`, `teams_get_athlete_gamelog`, and `teams_get_athlete_splits`.
  - `espn-news` (1 tool): `news_get_news`, plus resources `espn://reference/supported-leagues` and `espn://reference/capabilities`.
- **Deployment Profiles**: Updated `MCP_PROFILE` / `--profile` supporting `full` (all 33 tools), `games` (20 tools), `teams` (12 tools), `news` (1 tool), and `readonly` (all 33 tools).
- **SSRFSafeAsyncTransport Worker Thread Offload & Docstrings**: Moved DNS destination validation off the event loop via `await asyncio.to_thread(_validate_hostname_dns, hostname)`, added PEP 257 docstring to `handle_async_request`, and added regression test asserting `dns_thread != loop_thread`.
- **Defensive Formatter Hardening**:
  - Game summary: un-nulled leaders by handling team-nested categories and flat category lists; enriched against-the-spread (ATS) lines, favorites, and moneylines joined from `pickcenter` by team ID.
  - Team metadata & roster: added `franchise.venue` fallback, filtered completed games from `next_event`, and added rank/slot/jersey number fallbacks for depth charts.
  - Statistics & splits: handled opponent categories as direct lists; mapped `splitCategories` with labels to return concrete split numbers.
  - Calendar & leaderboards: resolved `$ref` index endpoints to active competition dates on `calendar/ondays`; trimmed multi-category athlete and team leader payloads to top 10 per category.
- **Hierarchical Middleware Pipeline**: Parent audit & readonly gates, child guardrails for games and teams.
- **Dynamic Tool Search**: Added opt-in `MCP_ENABLE_TOOL_SEARCH` / `--enable-tool-search` using `RegexSearchTransform` while preserving flat `tools/list` default wire format.
- **Documentation Bibles & Live Doc MCP**: Added `llms.txt` references (`https://gofastmcp.com/llms.txt`, `https://modelcontextprotocol.io/llms.txt`) and live documentation MCP server endpoints.

### Changed
- Refactored tool names to use domain prefixes (`games_*`, `teams_*`, `news_*`) with flat backwards-compatible function re-exports.
- 100.00% statement test coverage across all domain sub-servers, client methods, and middleware (70 tests passing).
- Zero breaking OpenAPI drift across 34 upstream spec endpoints.

## [1.1.1] - 2026-09-12

### Added
- **Testing Pyramid Modernization**: Added `tests/test_e2e_live.py` with `@pytest.mark.e2e` for safe, opt-in live trial verification with multi-layer secret redaction.
- **Config**: Added pytest marker `e2e` and standard `addopts` with `-m 'not e2e'` and `pythonpath = ["src", "."]`.

### Removed
- **Ad-Hoc Standalone Scripts**: Retired legacy `scripts/smoke_test.py`, `scripts/live_smoke_test.py`, and internal runners in favor of standard pytest test suites.

## [1.1.0] - 2026-09-11

### Added
- **Stateless Streamable HTTP Transport**: Added `--stateless` / `--no-stateless` and `--json-response` / `--no-json-response` CLI options and environment variables `MCP_STATELESS_HTTP` and `MCP_JSON_RESPONSE` per Model Context Protocol Spec 2026-07-28 (SEP-1049).
- **Wire-Level Protocol Verification**: Added `tests/test_protocol.py` exercising modern 2026-07-28 discovery entrypoint (`server/discover`), required `_meta` capabilities envelope, and routing headers (`MCP-Protocol-Version`, `Mcp-Method`, `Mcp-Name`).
- **ASGI Dispatch Testing**: Added `test_server_streamable_http_dispatch` in `tests/test_server.py` verifying real ASGI dispatch through `tools/call` with `httpx.MockTransport` backend.

### Fixed
- **Polymorphic Container Hardening**: Added defensive `isinstance(..., list)` and `isinstance(..., dict)` checks before indexing nested ESPN responses (odds, rosters, depth charts).
- **Client Exception Formatting**: Fast-failed 4xx client errors with `ESPNValidationError` and sanitized secret redaction across log tracebacks.

### Changed
- Explicit `open_world_hint=True` annotations across read-only inspection tools.
- 100% statement and branch test coverage across the entire test pyramid (31 unit tests passing).

## [1.0.3] - 2026-09-07

### Fixed
- Corrected container `ENTRYPOINT` in `Dockerfile` to `espn-mcp` (resolving startup failure on `ghcr.io` image).
- Hardened AST visitor in `scripts/check_openapi_drift.py` to reject unrecognized call receivers.

### Changed
- Upgraded container image to a hardened multi-stage build running as non-root user `mcp` with pre-built virtualenv, aligning with Snowflake and Sigma fleet standards.

## [1.0.2] - 2026-09-04

### Added
- Enterprise Agent Skill definition (`skills/espn-mcp/SKILL.md`) for autonomous sports analytics, odds comparison, matchup predictions, and prediction market resolution.
- Live 1-by-1 endpoint smoke test runner (`scripts/live_smoke_test.py`) validating all 10 tools against live ESPN REST services with latency tracking.

### Changed
- Hardened default HTTP server host binding to loopback `127.0.0.1` (configurable via `ESPN_HOST`).
- Replaced hardcoded client `User-Agent` version with dynamic `importlib.metadata` resolution.
- Added strict Pydantic numeric validation bounds to `ESPNConfig` (`timeout_seconds` 1.0–300.0s, `max_retries` 0–10).
- Explicit `SystemExit(0)` dispatch during graceful SIGINT/SIGTERM server shutdown.

### Fixed
- Supported both dictionary and list-of-formations payloads from ESPN in `_format_depth_chart`.
- Documented return types in `ESPNAPIError.to_dict()` and `ESPNClient._make_request()`.
- Addressed 100% of CodeRabbit automated code quality and security review findings (0 findings remaining).

## [1.0.1] - 2026-09-04

### Changed
- Added `mcp-name` ownership verification metadata to `README.md` for official Model Context Protocol Registry publishing.

## [1.0.0] - 2026-09-03

### Added
- **Complete ESPN Sports MCP Server**: Initial production release supporting live scores, boxscores, odds, standings, news, rankings, rosters, depth charts, and historical schedules.
- **10 Enterprise Sports Tools**:
  - `get_scoreboard`: Real-time scoreboards, period/clock, broadcasts, and starting probables with date, week, and Top 25 filtering.
  - `get_game_summary`: Comprehensive summary with consensus betting lines (DraftKings, Caesars, ESPN BET), matchup predictor (FPI/BPI), live win probability curve, season head-to-head series, last 5 games, team statistics, and injuries.
  - `get_player_stats`: Individual boxscore performance metrics (batting, pitching, passing, rushing, receiving, scoring).
  - `get_standings`: Division, conference, and league standings with historical season queries.
  - `get_news`: Breaking sports headlines, injury designations, and roster moves.
  - `get_rankings`: Top 25 national polls (AP Top 25, Coaches Poll, College Football Playoff rankings).
  - `get_team_roster`: Active team rosters by position with jersey numbers and experience.
  - `get_team_depth_chart`: Starter/backup positional hierarchy (QB1/QB2/RB1/RB2).
  - `get_team_schedule`: Full season game calendar with past game scores and upcoming fixtures.
  - `get_athlete_overview`: Biographical info, season/career split statistics, recent game logs, and rotowire notes.
- **Resilient Transport & Routing**:
  - Default routing to `https://site.web.api.espn.com` with HTTPS CDN mirror bypassing Akamai TLS fingerprint filters.
  - Asynchronous connection pool (`httpx.AsyncClient`) with jittered exponential backoff for HTTP 429/5xx errors.
  - Sport/league alias normalization supporting 30+ aliases (`mlb`, `nfl`, `nba`, `wnba`, `cfb`, `cbb`, `nhl`, `epl`, `mls`, `ucl`, etc.).
  - Deterministic catalog caching hints (`CacheHint(ttl_ms=3600000)`) per SEP-2549.
  - Graceful POSIX `SIGTERM`/`SIGINT` kernel exit interceptor (`os._exit(0)`).
  - Regex secret and credential scrubbing (`redact_secrets`).
- **Comprehensive Quality Gates**:
  - 100.0% statement test coverage across all modules.
  - Strict type checking (`mypy --strict`).
  - Automated tool contract validation, OpenAPI route drift monitoring, and stdio handshake smoke tests.

