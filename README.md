<!-- mcp-name: io.github.christianclaudio/espn -->

# 🏈 mcp-server-espn

[![CI](https://github.com/christianclaudio/mcp-server-espn/actions/workflows/ci.yml/badge.svg)](https://github.com/christianclaudio/mcp-server-espn/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/mcp-server-espn)](https://pypi.org/project/mcp-server-espn/)
[![Python](https://img.shields.io/pypi/pyversions/mcp-server-espn)](https://pypi.org/project/mcp-server-espn/)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen.svg)](https://github.com/christianclaudio/mcp-server-espn)
[![CodeRabbit Reviews](https://img.shields.io/coderabbit/prs/github/christianclaudio/mcp-server-espn?utm_source=oss&utm_medium=github&utm_campaign=christianclaudio%2Fmcp-server-espn&labelColor=171717&color=FF570A&link=https%3A%2F%2Fcoderabbit.ai&label=CodeRabbit+Reviews)](https://coderabbit.ai)

> **Enterprise-grade Model Context Protocol (MCP) server for live and historical sports analytics, consensus betting odds, and predictions via ESPN.**  
> Equips AI agents with real-time sports intelligence, live win probabilities, in-depth boxscore statistics, roster hierarchies, and matchup analytics.

---

## ⚠️ Disclaimers & Fair Use Notice

> [!IMPORTANT]
> **Community Project Disclaimer**  
> `mcp-server-espn` is an independent open-source community project. It is **not** affiliated with, sponsored by, endorsed by, or supported by ESPN Inc. or The Walt Disney Company. *"ESPN"* is a trademark of ESPN Inc. All data provided via ESPN's public REST endpoints is intended for educational, research, and personal non-commercial use.

---

## 💡 Why This Exists

Autonomous sports analysis requires high-velocity, structured, and resilient data feeds:
1. **Live Game State & Win Probability:** Real-time game events (turnovers, scoring plays, pitching changes) shift momentum and expected outcomes dynamically.
2. **Key Player Injuries & Depth Chart Swaps:** An in-game injury or substitution fundamentally alters team efficiency and tactical matchups.
3. **Consensus Odds & Predictive Models:** Aggregating consensus sportsbook lines (DraftKings, Caesars, ESPN BET) alongside predictive metrics (FPI, BPI) powers deep statistical game evaluations.

`mcp-server-espn` provides a unified, hardened Model Context Protocol interface directly to ESPN's public sports data endpoints.

---

## 🏟️ System Architecture

```mermaid
graph TD
    Client["AI Agent / MCP Client<br>(Antigravity, Claude, Codex, Cortex)"]
    Gateway["Root FastMCP Gateway (espn-mcp)<br>(stdio / Streamable HTTP)"]
    ParentMW["Parent Middleware Pipeline<br>(ParentAuditMiddleware & ReadOnlyGateMiddleware)"]
    GamesSub["Sub-Server: espn-games<br>(games_* | GamesDomainGuard)"]
    TeamsSub["Sub-Server: espn-teams<br>(teams_* | TeamsDomainGuard)"]
    NewsSub["Sub-Server: espn-news<br>(news_* | Resources)"]
    ClientHandler["Hardened ESPN AsyncClient<br>(Connection Pool & 429 Jitter Backoff)"]
    ESPN["ESPN Public REST CDN<br>(https://site.web.api.espn.com)"]

    Client <-->|"JSON-RPC (tools/list, tools/call)"| Gateway
    Gateway --> ParentMW
    ParentMW --> GamesSub
    ParentMW --> TeamsSub
    ParentMW --> NewsSub
    GamesSub & TeamsSub & NewsSub <-->|"API Methods"| ClientHandler
    ClientHandler <-->|"HTTPS REST Mirror"| ESPN
```

---

## 🚀 FastMCP 4 Server Composition

`mcp-server-espn` implements canonical FastMCP 4 Server Composition via `root.mount(..., namespace="...")`:
* **Domain Sub-Servers**: Partitioned into `espn-games` (`games_*`), `espn-teams` (`teams_*`), and `espn-news` (`news_*`).
* **Hierarchical Middleware**:
  * **Parent**: `ParentAuditMiddleware` (timing logs, audit trails, and secret scrubbing) and `ReadOnlyGateMiddleware` (fail-closed read-only enforcement).
  * **Child**: `GamesDomainGuardMiddleware` (query limit validation <= 100) and `TeamsDomainGuardMiddleware` (team and athlete identifier validation).
* **Focused Profiles**: Run lightweight surfaces via `--profile full|games|teams|news|readonly` (`MCP_PROFILE`):
  * `full` (default): All 33 domain tools and resources mounted.
  * `games`: Scores, summaries, schedules, standings, rankings, transactions, league leaders, draft, live play-by-play, situations, odds, probabilities, predictor, calendar, futures, power index (20 tools).
  * `teams`: Rosters, depth charts, player stats, athlete profiles, bio, stats, gamelog, splits, search, list teams, team detail, team statistics (12 tools).
  * `news`: League news and reference resources (1 tool).
  * `readonly`: Read-only enforcement across all routes.
* **Opt-In Tool Search**: Preserves standard flat `tools/list` by default for seamless client compatibility, while enabling regex search transforms via `--enable-tool-search` (`MCP_ENABLE_TOOL_SEARCH`).

---

## 📈 Sports Intelligence Workflows

### Workflow 1: Live In-Game Win Probability & Situation Tracking
1. **Poll Active Games:** Agent calls `games_get_scoreboard(sport="football", league="nfl")` to identify close games in the 2nd half.
2. **Fetch In-Game Situation & Win Probability:** Call `games_get_game_situation(sport="football", league="nfl", event_id="401872947")` and `games_get_win_probabilities(sport="football", league="nfl", event_id="401872947")` to inspect down/distance, red zone state, and live win expectancy curve.
3. **Inspect Play-by-Play:** Call `games_get_play_by_play(sport="football", league="nfl", event_id="401872947")` for drive-by-drive sequencing and scoring event logs.

### Workflow 2: Athlete Deep Dive & Fantasy Valuation
1. **Bio & Career Context:** Call `teams_get_athlete_bio(sport="football", league="nfl", athlete_id="12483")` to retrieve draft capital, college pedigree, and physical metrics.
2. **Recent Gamelog & Splits:** Pull `teams_get_athlete_gamelog` and `teams_get_athlete_splits` to analyze performance trends against specific defensive schemes and venue conditions.
3. **League-Wide Leaderboard Standing:** Call `games_get_leaders_by_athlete(sport="football", league="nfl", category="passing", sort="yards")` to evaluate percentile rankings.

---

## 🏟️ Supported Sports & Leagues Reference Matrix

The server supports canonical sport/league slug pairs and auto-normalizes popular shortcuts:

| Sport Slug | League Slug | Recognized Shortcuts / Aliases | Common Display Name |
| :--- | :--- | :--- | :--- |
| `football` | `nfl` | `nfl` | National Football League |
| `football` | `college-football` | `cfb`, `ncaa-football`, `fbs` | NCAA College Football |
| `basketball` | `nba` | `nba` | National Basketball Association |
| `basketball` | `mens-college-basketball` | `cbb`, `ncaa-basketball` | NCAA Men's College Basketball |
| `basketball` | `womens-college-basketball` | `wbb`, `ncaa-womens-basketball` | NCAA Women's Basketball |
| `basketball` | `wnba` | `wnba` | Women's National Basketball Association |
| `baseball` | `mlb` | `mlb` | Major League Baseball |
| `hockey` | `nhl` | `nhl` | National Hockey League |
| `soccer` | `eng.1` | `epl`, `premier-league` | English Premier League |
| `soccer` | `usa.1` | `mls` | Major League Soccer |
| `soccer` | `uefa.champions` | `ucl`, `champions-league` | UEFA Champions League |
| `soccer` | `esp.1` | `la-liga` | Spanish La Liga |
| `soccer` | `ita.1` | `serie-a` | Italian Serie A |
| `soccer` | `ger.1` | `bundesliga` | German Bundesliga |
| `soccer` | `fra.1` | `ligue-1` | French Ligue 1 |

---

## 📊 Tool Suite (33 Domain Tools)

All tools implement explicit MCP 2.0 annotations (`readOnlyHint=True`, `idempotentHint=True`):

| Domain | Tool | Parameters | Description |
| :--- | :--- | :--- | :--- |
| **Games** | `games_get_scoreboard` | `sport`, `league`, `date`, `week`, `season_type`, `group`, `limit` | Live scores, state (`pre`/`in`/`post`), period/clock, TV broadcasts, starting probables. |
| **Games** | `games_get_game_summary` | `sport`, `league`, `event_id` | Consensus betting lines, matchup predictor win %, ATS, scoring plays, drives, leaders, momentum. |
| **Games** | `games_get_team_schedule` | `sport`, `league`, `team_id`, `season` | Full regular season and postseason schedule with historical game results and scores. |
| **Games** | `games_get_standings` | `sport`, `league`, `season` | Division, conference, and overall league standings, win-loss records, games back, and win percentages. |
| **Games** | `games_get_rankings` | `sport`, `league` | Top 25 national polls and rankings (AP Top 25, Coaches Poll, College Football Playoff). |
| **Games** | `games_get_transactions` | `sport`, `league`, `limit` | League-wide transactions, roster trades, waivers, signings, and releases. |
| **Games** | `games_get_leaders_by_athlete` | `sport`, `league`, `limit`, `category`, `sort` | Statistical leaderboards across players in a league. |
| **Games** | `games_get_leaders_by_team` | `sport`, `league`, `limit`, `category`, `sort` | Team statistical rankings and leaderboards. |
| **Games** | `games_get_league_groups` | `sport`, `league` | League conference, division, and structural group hierarchies. |
| **Games** | `games_get_league_events` | `sport`, `league`, `dates` | League-wide calendar of scheduled events, optionally filtered by date. |
| **Games** | `games_get_league_draft` | `sport`, `league`, `season` | League draft rounds, team selections, and pick results. |
| **Games** | `games_get_scoreboard_header` | `sport`, `league` | Live ticker scoreboard header data across games for a sport and league. |
| **Games** | `games_get_event_odds` | `sport`, `league`, `event_id`, `competition_id` | Sportsbook consensus and provider odds (spreads, totals, moneylines). |
| **Games** | `games_get_play_by_play` | `sport`, `league`, `event_id`, `limit`, `page` | Play-by-play sequence, clock, scoring, and drive events. |
| **Games** | `games_get_game_situation` | `sport`, `league`, `event_id` | Real-time in-game situation (down, distance, yardline, possession, red zone). |
| **Games** | `games_get_win_probabilities` | `sport`, `league`, `event_id` | Live and historical win probability curves across game progression. |
| **Games** | `games_get_game_predictor` | `sport`, `league`, `event_id` | Pre-game and in-game matchup predictor and projection metrics. |
| **Games** | `games_get_calendar` | `sport`, `league`, `dates` | League schedule calendar and active event dates across a season. |
| **Games** | `games_get_futures` | `sport`, `league`, `season` | Season futures betting markets (championship odds, win totals). |
| **Games** | `games_get_power_index` | `sport`, `league`, `season` | Team power index (FPI / BPI) ratings and efficiency metrics. |
| **Teams** | `teams_search` | `query`, `type`, `limit` | Global search for athletes and teams by name/keyword (`type="player"` or `"team"`). |
| **Teams** | `teams_list_teams` | `sport`, `league` | Directory of all franchises/teams in a specified league with IDs, names, and logos. |
| **Teams** | `teams_get_team` | `sport`, `league`, `team_id` | Team detail overview, venue, record, standings summary, and upcoming scheduled event. |
| **Teams** | `teams_get_team_statistics` | `sport`, `league`, `team_id` | Comprehensive team and opponent statistical category splits (passing, rushing, etc.). |
| **Teams** | `teams_get_team_roster` | `sport`, `league`, `team_id` | Full active roster grouped by position, coach info, jersey numbers, and injury status. |
| **Teams** | `teams_get_team_depth_chart` | `sport`, `league`, `team_id` | Positional starter/backup hierarchy (QB1, QB2, etc.) to model injury substitution impacts. |
| **Teams** | `teams_get_player_stats` | `sport`, `league`, `event_id` | Boxscore statistics for individual athletes across game categories. |
| **Teams** | `teams_get_athlete_overview` | `sport`, `league`, `athlete_id` | Athlete biographical info, season/career stats, game logs, next game, and rotowire notes. |
| **Teams** | `teams_get_athlete_bio` | `sport`, `league`, `athlete_id` | Detailed athlete background, draft history, birth details, college pedigree. |
| **Teams** | `teams_get_athlete_stats` | `sport`, `league`, `athlete_id`, `season` | Full seasonal and career category statistics for an athlete. |
| **Teams** | `teams_get_athlete_gamelog` | `sport`, `league`, `athlete_id`, `season` | Game-by-game performance log for an athlete across a season. |
| **Teams** | `teams_get_athlete_splits` | `sport`, `league`, `athlete_id`, `season` | Situational statistical splits (home/away, turf/grass, monthly, vs opponents). |
| **News** | `news_get_news` | `sport`, `league`, `limit` | Recent news headlines, injury designations, and breaking roster analysis. |

---

## 🏃 Quickstart & Installation

### 1. Run Directly via `uvx` (Zero Install)
```bash
uvx mcp-server-espn
```

### 2. Install via `pip` or `uv`
```bash
# Using pip
pip install mcp-server-espn

# Using uv
uv add mcp-server-espn
```

### 3. Run via Docker
```bash
docker run --rm -i ghcr.io/christianclaudio/mcp-server-espn:latest
```

---

## 🎛️ Engine Configuration

| Variable | CLI Flag | Default | Description |
| :--- | :--- | :--- | :--- |
| `ESPN_BASE_URL` | — | `https://site.web.api.espn.com` | Target ESPN REST CDN base URL (bypasses Akamai TLS filter) |
| `ESPN_TIMEOUT_SECONDS` | — | `30.0` | HTTP request timeout in seconds |
| `ESPN_MAX_RETRIES` | — | `3` | Maximum retry attempts with jittered exponential backoff |
| `ESPN_MCP_READONLY` | — | `0` | Restrict server strictly to read-only inspection tools |
| `ESPN_MCP_PROFILE` | `--profile` | `full` | Domain sub-server profile: `full`, `games`, `teams`, `news`, `readonly` |
| `ESPN_MCP_ENABLE_TOOL_SEARCH` | `--enable-tool-search` | `0` | Replace flat tool catalog with dynamic regex search transform |

---

## 🎮 Client Integration Guides

<details open>
<summary><b>🧡 Claude Desktop</b></summary>

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "espn": {
      "command": "uvx",
      "args": ["mcp-server-espn"],
      "env": {
        "ESPN_TIMEOUT_SECONDS": "20.0"
      }
    }
  }
}
```

For **Claude Code CLI**:
```bash
claude mcp add espn -- uvx mcp-server-espn
```
</details>

<details>
<summary><b>♊ Google Antigravity & Gemini CLI</b></summary>

Add to `.agents/mcp_config.json` or `~/.gemini/config/mcp_config.json`:

```json
{
  "mcpServers": {
    "espn": {
      "command": "uvx",
      "args": ["mcp-server-espn"],
      "env": {
        "ESPN_TIMEOUT_SECONDS": "20.0"
      },
      "lazy": true
    }
  }
}
```
</details>

<details>
<summary><b>❄️ Snowflake Cortex</b></summary>

Add to `~/.snowflake/cortex/mcp.json`:

```json
{
  "mcpServers": {
    "espn": {
      "command": "uvx",
      "args": ["mcp-server-espn"],
      "env": {
        "ESPN_TIMEOUT_SECONDS": "20.0"
      },
      "lazy": true
    }
  }
}
```
</details>

<details>
<summary><b>⚡ Cursor IDE</b></summary>

Add to `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "espn": {
      "command": "uvx",
      "args": ["mcp-server-espn"]
    }
  }
}
```
</details>

<details>
<summary><b>💻 VS Code (Cline / Roo Code / Copilot Agent Mode)</b></summary>

Add to `cline_mcp_settings.json` or `.vscode/settings.json`:

```json
{
  "mcpServers": {
    "espn": {
      "command": "uvx",
      "args": ["mcp-server-espn"]
    }
  }
}
```
</details>

<details>
<summary><b>🌐 Local HTTP / Network Transport Mode</b></summary>

Launch the FastMCP server over modern Streamable HTTP:

```bash
python -m espn_mcp.server --transport streamable-http --host 127.0.0.1 --port 8000
```

Connect your local HTTP client to `http://127.0.0.1:8000/sse`.
</details>

---

## 📚 Canonical Documentation & Live Doc MCPs

When developing, hardening, or extending MCP servers, consult the official framework and protocol references:

* **FastMCP 4 Framework Reference**: [`https://gofastmcp.com/llms.txt`](https://gofastmcp.com/llms.txt) — Server composition (`mount`), hierarchical middleware, transforms (`ToolTransform`, `ToolSearch`), lifespans, and in-memory test clients.
* **Model Context Protocol Specification**: [`https://modelcontextprotocol.io/llms.txt`](https://modelcontextprotocol.io/llms.txt) — Official Spec (2026-07-28), wire-level JSON-RPC schemas, annotations, and transport framing.

### Live Documentation MCP Endpoints (SSE / Streamable HTTP)
Connect your AI coding agent directly to live documentation servers:
* **FastMCP Documentation Server**: `https://gofastmcp.com/mcp` (Tools: `search_fast_mcp`, `query_docs_filesystem_fast_mcp`, `submit_feedback`)
* **Anthropic MCP Documentation Server**: `https://modelcontextprotocol.io/mcp` (Tools: `search_model_context_protocol`, `query_docs_filesystem_model_context_protocol`, `submit_feedback`)

```json
{
  "mcpServers": {
    "fastmcp-docs": { "type": "sse", "url": "https://gofastmcp.com/mcp" },
    "mcp-official-docs": { "type": "sse", "url": "https://modelcontextprotocol.io/mcp" }
  }
}
```

---

## 🏆 Verification & Quality Gates

```bash
# Run unit test suite (100% statement coverage enforced)
pytest

# Static type safety & formatting
mypy --strict src/
ruff check --fix .
ruff format .

# Tool contract, drift & protocol conformance audits
python scripts/check_tool_contract.py
python scripts/check_openapi_drift.py
./scripts/check_conformance.sh
```

---

## 📜 License

Distributed under the [Apache-2.0 License](LICENSE).

