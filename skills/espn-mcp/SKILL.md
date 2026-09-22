---
name: espn-mcp
description: Enterprise Agent Skill for live and historical sports analytics, odds, predictions, rosters, and prediction market resolution via mcp-server-espn.
version: 1.1.0
---

# ESPN Sports Analytics MCP Server (`mcp-server-espn`) Agent Skill

This skill provides expert operating guidelines, architectural recipes, and best practices for AI agents orchestrating sports analytics, live score tracking, betting market analysis, and prediction market resolution (e.g. Kalshi, Polymarket) via `mcp-server-espn`.

---

## 🏗️ Architecture & Server Composition

The server is composed of three domain sub-servers mounted onto a root FastMCP gateway with hierarchical middleware:
- **`espn-games` (`games_*`)**: Live scores, game summaries, team schedules, standings, rankings, and `game_analysis` prompt.
- **`espn-teams` (`teams_*`)**: Team rosters, depth charts, player stats, athlete profiles, and `team_evaluation` prompt.
- **`espn-news` (`news_*`)**: League news, `espn://reference/supported-leagues`, and `espn://reference/capabilities`.

### Deployment Profiles (`--profile` / `ESPN_MCP_PROFILE`)
- `full` (default): All 10 domain tools and resources mounted.
- `games`: Focused on scores, summaries, schedules, standings, rankings (5 tools).
- `teams`: Focused on rosters, depth charts, player stats, athlete profiles (4 tools).
- `news`: Focused on news and reference resources (1 tool).
- `readonly`: Fail-closed read-only configuration.

### Dynamic Tool Search (`--enable-tool-search` / `ESPN_MCP_ENABLE_TOOL_SEARCH`)
- **Default (Flat Catalog)**: Preserves the standard flat `tools/list` schema for universal client compatibility (Claude, Cursor, Cortex, Antigravity).
- **Opt-In Tool Search**: When `--enable-tool-search` is passed or `ESPN_MCP_ENABLE_TOOL_SEARCH=1`, FastMCP mounts `RegexSearchTransform`, replacing flat listings with dynamic search meta-tools (`search_tools`, `call_tool`) to conserve context tokens in heavy agent loops.

---

## 🎯 Core Agent Recipes & Playbooks

### 1. Prediction Market Resolution & Fair-Value Arbitrage
When resolving or handicapping sports event contracts on prediction platforms:
- **Step 1: Discover Active Game**: Call `games_get_scoreboard(sport="baseball", league="mlb", date="YYYYMMDD")` to locate the target `event_id`, live game status (`state: "in"`, `"pre"`, or `"post"`), and current score.
- **Step 2: Extract Consensus Odds & Metrics**: Call `games_get_game_summary(sport=..., league=..., event_id=...)` to retrieve consensus betting lines (`spread`, `moneyline`, `over/under` from DraftKings/Caesars/ESPN BET) and the proprietary ESPN Matchup Predictor (win probability percentage).
- **Step 3: Analyze Injury & Depth Impacts**: Cross-reference `teams_get_team_depth_chart(sport=..., league=..., team_id=...)` with active injury designations in `teams_get_team_roster(sport=..., league=..., team_id=...)` to determine if key starters (e.g., QB1, Ace Pitcher) are ruled out.
- **Step 4: Synthesize Fair-Value Estimate**: Compare the implied probability from sportsbooks against the prediction market contract price to detect mispricings.

### 2. Live In-Game Momentum & Win Probability Tracking
- **Real-Time Clocks**: Inspect `games_get_scoreboard(...)` to parse quarter/period, remaining time, possession, and current down/distance or inning outs.
- **Live Win Probability**: Call `games_get_game_summary(sport=..., league=..., event_id=...)` to track the live `winprobability` curve across game progression and evaluate in-game swings.
- **Top Performers**: Call `teams_get_player_stats(sport=..., league=..., event_id=...)` to inspect live individual player boxscores (points, strikeouts, yards, touches).

### 3. Team Form, Streaks & Strength of Schedule
- **Standings & Streaks**: Invoke `games_get_standings(sport=..., league=...)` to evaluate conference/division ranking, differential, home/away splits, and current win/loss streaks.
- **Recent Results & Momentum**: Call `games_get_team_schedule(sport=..., league=..., team_id=..., season=...)` to review past 5 games, opponent quality, and margin of victory.
- **National Polls**: For college sports (NCAAF / NCAAB), call `games_get_rankings(sport="football", league="college-football")` to check AP Top 25 and CFP committee ranks.

### 4. Player Props & Fantasy Analysis
- **Career & Season Splits**: Call `teams_get_athlete_overview(sport=..., league=..., athlete_id=...)` to fetch statistical splits (home vs. away, turf vs. grass, vs. opponent).
- **Recent Rotowire Intelligence**: Review `rotowire_notes` inside athlete overview for beat reporter injury quotes and practice status.

---

## 🛠️ Tool Suite Reference (10 Domain Tools)

| Domain | Tool | Purpose | Annotations | Key Parameters |
| :--- | :--- | :--- | :--- | :--- |
| **Games** | `games_get_scoreboard` | Live & historical scores, statuses, TV broadcasts, starters | `readOnlyHint=True` | `sport`, `league`, `date`, `week`, `season_type`, `limit` |
| **Games** | `games_get_game_summary` | Consensus betting lines, predictor win %, injuries, series | `readOnlyHint=True` | `sport`, `league`, `event_id` |
| **Games** | `games_get_team_schedule` | Full season schedule and past game scores for a team | `readOnlyHint=True` | `sport`, `league`, `team_id`, `season` |
| **Games** | `games_get_standings` | Division, conference, and league standings with streaks | `readOnlyHint=True` | `sport`, `league`, `season` |
| **Games** | `games_get_rankings` | Top 25 national polls (AP, Coaches, CFP) for college sports | `readOnlyHint=True` | `sport`, `league` |
| **Teams** | `teams_get_team_roster` | Active squad roster, jersey numbers, and injuries | `readOnlyHint=True` | `sport`, `league`, `team_id` |
| **Teams** | `teams_get_team_depth_chart`| Positional starter / backup tiers (QB1, QB2, etc.) | `readOnlyHint=True` | `sport`, `league`, `team_id` |
| **Teams** | `teams_get_player_stats` | Boxscore statistics for individual athletes across game | `readOnlyHint=True` | `sport`, `league`, `event_id` |
| **Teams** | `teams_get_athlete_overview`| Career splits, recent game logs, and fantasy notes | `readOnlyHint=True` | `sport`, `league`, `athlete_id` |
| **News** | `news_get_news` | Headlines, breaking injury analysis, and roster moves | `readOnlyHint=True` | `sport`, `league`, `limit` |

---

## ⚡ Agent Operating Guidelines

1. **Domain Shortcut & Alias Normalization**:
   Tools accept both explicit pairs (`sport="baseball", league="mlb"`) and common shorthand aliases (`sport="mlb", league="mlb"` or `sport="nfl", league="nfl"`). The server automatically normalizes them.
2. **Deterministic Caching (SEP-2549)**:
   The server implements catalog caching with `ttl_ms=3600000` (1 hr) on metadata discovery (`tools/list`, `prompts/list`, `resources/list`, etc.). Agents should avoid redundant rapid-polling within cache TTLs.
3. **Graceful Fail-Closed Handling**:
   All tools return `{ "status": "success", "data": ... }` or `{ "status": "error", "message": "<redacted error message>" }`. Agents should inspect `status` and handle errors cleanly without crashing.
4. **Child Domain Guardrails**:
   - `GamesDomainGuardMiddleware` enforces batch limit $\le$ 100 on scoreboard queries.
   - `TeamsDomainGuardMiddleware` enforces non-empty team and athlete identifiers.
