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
  * `full` (default): All 15 domain tools and resources mounted.
  * `games`: Scores, summaries, schedules, standings, rankings, transactions (6 tools).
  * `teams`: Rosters, depth charts, player stats, athlete profiles, search, list teams, team detail, team statistics (8 tools).
  * `news`: League news and reference resources (1 tool).
  * `readonly`: Read-only enforcement across all routes.
* **Opt-In Tool Search**: Preserves standard flat `tools/list` by default for seamless client compatibility, while enabling regex search transforms via `--enable-tool-search` (`MCP_ENABLE_TOOL_SEARCH`).

---

## 📈 Sports Intelligence Workflows

### Workflow 1: Live In-Game Win Probability & Injury Impact
1. **Poll Active Games:** Agent calls `games_get_scoreboard(sport="football", league="nfl")` to identify close games in the 2nd half.
2. **Fetch Matchup Predictor & Injuries:** Call `games_get_game_summary(sport="football", league="nfl", event_id="401547432")` to retrieve ESPN's live win probability curve, consensus spread, and active injury reports.
3. **Inspect Player Boxscore Metrics:** Use `teams_get_player_stats(sport="football", league="nfl", event_id="401547432")` to analyze key individual performances (passing yards, completion rates, defensive stops).

### Workflow 2: Pre-Game Roster & Depth Chart Matchup Preview
1. **Analyze Lineups:** Call `teams_get_team_depth_chart(sport="baseball", league="mlb", team_id="10")` to verify probable starters and positional depth.
2. **Review Recent Momentum:** Pull `games_get_team_schedule(sport="baseball", league="mlb", team_id="10")` and `games_get_standings(sport="baseball", league="mlb")` to evaluate streaks and divisional standing.
3. **Compare Consensus Betting Lines:** Query `games_get_game_summary` to evaluate consensus moneyline and over/under spreads across major sportsbooks.

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

## 📊 Tool Suite (15 Domain Tools)

All tools implement explicit MCP 2.0 annotations (`readOnlyHint=True`, `idempotentHint=True`):

| Domain | Tool | Parameters | Description |
| :--- | :--- | :--- | :--- |
| **Games** | `games_get_scoreboard` | `sport`, `league`, `date`, `week`, `season_type`, `group`, `limit` | Live scores, state (`pre`/`in`/`post`), period/clock, TV broadcasts, starting probables. |
| **Games** | `games_get_game_summary` | `sport`, `league`, `event_id` | Consensus betting lines, matchup predictor win %, ATS, scoring plays, drives, leaders, momentum. |
| **Games** | `games_get_team_schedule` | `sport`, `league`, `team_id`, `season` | Full regular season and postseason schedule with historical game results and scores. |
| **Games** | `games_get_standings` | `sport`, `league`, `season` | Division, conference, and overall league standings, win-loss records, games back, and win percentages. |
| **Games** | `games_get_rankings` | `sport`, `league` | Top 25 national polls and rankings (AP Top 25, Coaches Poll, College Football Playoff). |
| **Games** | `games_get_transactions` | `sport`, `league`, `limit` | League-wide transactions, roster trades, waivers, signings, and releases. |
| **Teams** | `teams_search` | `query`, `type`, `limit` | Global search for athletes and teams by name/keyword (`type="player"` or `"team"`). |
| **Teams** | `teams_list_teams` | `sport`, `league` | Directory of all franchises/teams in a specified league with IDs, names, and logos. |
| **Teams** | `teams_get_team` | `sport`, `league`, `team_id` | Team detail overview, venue, record, standings summary, and upcoming scheduled event. |
| **Teams** | `teams_get_team_statistics` | `sport`, `league`, `team_id` | Comprehensive team and opponent statistical category splits (passing, rushing, etc.). |
| **Teams** | `teams_get_team_roster` | `sport`, `league`, `team_id` | Full active roster grouped by position, coach info, jersey numbers, and injury status. |
| **Teams** | `teams_get_team_depth_chart` | `sport`, `league`, `team_id` | Positional starter/backup hierarchy (QB1, QB2, etc.) to model injury substitution impacts. |
| **Teams** | `teams_get_player_stats` | `sport`, `league`, `event_id` | Boxscore statistics for individual athletes across game categories. |
| **Teams** | `teams_get_athlete_overview` | `sport`, `league`, `athlete_id` | Athlete biographical info, season/career stats, game logs, next game, and rotowire notes. |
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

