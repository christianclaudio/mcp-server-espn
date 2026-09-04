# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

