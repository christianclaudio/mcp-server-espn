# 🏈 mcp-server-espn

[![CI](https://github.com/christianclaudio/mcp-server-espn/actions/workflows/ci.yml/badge.svg)](https://github.com/christianclaudio/mcp-server-espn/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/mcp-server-espn)](https://pypi.org/project/mcp-server-espn/)
[![Python](https://img.shields.io/pypi/pyversions/mcp-server-espn)](https://pypi.org/project/mcp-server-espn/)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen.svg)](https://github.com/christianclaudio/mcp-server-espn)
[![CodeRabbit Reviews](https://img.shields.io/coderabbit/prs/github/christianclaudio/mcp-server-espn?utm_source=oss&utm_medium=github&utm_campaign=christianclaudio%2Fmcp-server-espn&labelColor=171717&color=FF570A&link=https%3A%2F%2Fcoderabbit.ai&label=CodeRabbit+Reviews)](https://coderabbit.ai)

> **Enterprise-grade Model Context Protocol (MCP) server for live and historical sports analytics, consensus betting odds, and predictions via ESPN.**  
> Built for autonomous agents executing live sports intelligence, odds comparisons, injury impact modeling, and prediction market trading (e.g. against Kalshi, Polymarket).

---

## ⚠️ Disclaimers & Fair Use Notice

> [!IMPORTANT]
> **Community Project Disclaimer**  
> `mcp-server-espn` is an independent open-source community project. It is **not** affiliated with, sponsored by, endorsed by, or supported by ESPN Inc. or The Walt Disney Company. *"ESPN"* is a trademark of ESPN Inc. All data provided via ESPN's public REST endpoints is intended for educational, research, and personal non-commercial use.

---

## 💡 Why This Exists & Prediction Market Synergy

Sports prediction markets (e.g., Kalshi, Polymarket) trade heavily on micro-second information shifts:
1. **Live Win Probability Dislocation:** Real-time game events (turnovers, red-zone stops, pitching changes) rapidly alter expected win probabilities before market orders adjust.
2. **Key Player Injuries & Depth Chart Swaps:** An in-game QB, point guard, or ace pitcher departure fundamentally alters spread expectations.
3. **Consensus Odds vs. Market Implied Probability:** Comparing institutional consensus sportsbook lines (DraftKings, Caesars, ESPN BET) against peer-to-peer prediction contract prices uncovers mispriced assets.

`mcp-server-espn` provides the verified sports ground-truth feeds that AI agents need to detect these opportunities autonomously.

---

## 🏗️ Architecture & Cross-MCP Workflow

```mermaid
graph TD
    subgraph AgentRuntime["Autonomous Agent Orchestrator"]
        LLM["AI Agent / LLM<br>(Antigravity, Claude, Hermes)"]
    end

    subgraph MCPServers["Model Context Protocol Fleet"]
        ESPN_MCP["mcp-server-espn<br>(FastMCP Engine)"]
        KALSHI_MCP["mcp-server-kalshi<br>(Prediction Markets)"]
    end

    subgraph DataFeeds["Public REST & Market Gateways"]
        ESPN_CDN["ESPN Web REST CDN<br>(Scores, Predictor, Odds, Depth Charts)"]
        KALSHI_API["Kalshi Exchange API<br>(Orderbook Depth, Limit Orders)"]
    end

    LLM <-->|"1. get_game_summary / get_player_stats"| ESPN_MCP
    ESPN_MCP <-->|"High-Throughput Async HTTP"| ESPN_CDN
    LLM <-->|"2. Analyze Spread / Win Prob Dislocation"| LLM
    LLM <-->|"3. Query Orderbook & Place Orders"| KALSHI_MCP
    KALSHI_MCP <-->|"Authenticated REST"| KALSHI_API
```

---

## 🤖 Agent Workflows & Cross-MCP Synergy Recipes

### Recipe 1: Real-Time In-Game Arbitrage & Injury Dislocation
Pair `mcp-server-espn` with `mcp-server-kalshi` to detect mispriced in-game win contracts:

1. **Poll Game State:** Agent invokes `get_scoreboard(sport="football", league="nfl")` to identify active games in the 2nd half.
2. **Fetch Predictor & In-Game Injuries:** Call `get_game_summary(sport="football", league="nfl", event_id="401547432")` to retrieve ESPN's live win probability, consensus spread, and active injury reports.
3. **Cross-Reference Prediction Market:** Agent calls Kalshi's `get_market(ticker="KXNFLGAME-24DEC15-KCBUF-KC")` to examine current Yes/No contract prices and orderbook depth.
4. **Identify Spread Discrepancy:** If ESPN's live model calculates an 82% win probability following an opponent turnover, but the prediction market Yes contract is trading at 71¢, the agent flags an expected value (+EV) opportunity.

### Recipe 2: Pre-Game Line Movement & Consensus Odds Validation
1. Use `get_game_summary` to pull consensus odds from DraftKings, Caesars, and ESPN BET.
2. Cross-reference starting pitchers or starting quarterbacks using `get_team_depth_chart(sport="baseball", league="mlb", team_id="10")`.
3. Synthesize consensus spread/moneyline with recent team momentum (last 5 games) and head-to-head records.

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

## 🛠️ Tool Suite (10 Tools)

All tools implement explicit MCP 2.0 annotations (`readOnlyHint=True`, `idempotentHint=True`):

| Tool | Parameters | Description |
| :--- | :--- | :--- |
| `get_scoreboard` | `sport`, `league`, `date`, `week`, `season_type`, `group`, `limit` | Live scores, state (`pre`/`in`/`post`), period/clock, TV broadcasts, starting probables. |
| `get_game_summary` | `sport`, `league`, `event_id` | Consensus betting lines (DraftKings, Caesars, ESPN BET), matchup predictor, live win probability curve, season head-to-head series, momentum (last 5 games), injuries. |
| `get_player_stats` | `sport`, `league`, `event_id` | Boxscore statistics for individual athletes (batting, pitching, passing, rushing, receiving, scoring). |
| `get_standings` | `sport`, `league`, `season` | Division, conference, and overall league standings, win-loss records, games back, and win percentages. |
| `get_news` | `sport`, `league`, `limit` | Recent news headlines, injury designations, and breaking roster analysis. |
| `get_rankings` | `sport`, `league` | Top 25 national polls and rankings (AP Top 25, Coaches Poll, College Football Playoff). |
| `get_team_roster` | `sport`, `league`, `team_id` | Full active roster grouped by position, jersey numbers, experience, and injury status. |
| `get_team_depth_chart` | `sport`, `league`, `team_id` | Positional starter/backup hierarchy (QB1, QB2, RB1, RB2) to model injury substitution impacts. |
| `get_team_schedule` | `sport`, `league`, `team_id`, `season` | Full regular season and postseason schedule with historical game results and scores. |
| `get_athlete_overview` | `sport`, `league`, `athlete_id` | Athlete biographical info, season/career split statistics, recent game logs, next game, and rotowire notes. |

---

## 📦 Installation & Quickstart

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

## ⚙️ Configuration

| Variable | Default | Description |
| :--- | :--- | :--- |
| `ESPN_BASE_URL` | `https://site.web.api.espn.com` | Target ESPN REST CDN base URL (bypasses Akamai TLS filter) |
| `ESPN_TIMEOUT_SECONDS` | `30.0` | HTTP request timeout in seconds |
| `ESPN_MAX_RETRIES` | `3` | Maximum retry attempts with jittered exponential backoff |
| `ESPN_MCP_READONLY` | `0` | Restrict server strictly to read-only inspection tools |

---

## 🔌 Integration Guides for AI Assistants & IDEs

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

## 🧪 Verification & Quality Gates

```bash
# Run unit test suite (100% statement coverage enforced)
pytest

# Static type safety & formatting
mypy --strict src/
ruff check --fix .
ruff format .

# Tool contract & drift audits
python scripts/check_tool_contract.py
python scripts/check_openapi_drift.py
python scripts/smoke_test.py
```

---

## 📄 License

Distributed under the [Apache-2.0 License](LICENSE).

