---
name: espn-mcp
description: Enterprise Agent Skill for live and historical sports analytics, odds, predictions, rosters, and prediction market resolution via mcp-server-espn.
version: 1.0.1
---

# ESPN Sports Analytics MCP Server (`mcp-server-espn`) Agent Skill

This skill provides expert operating guidelines, architectural recipes, and best practices for AI agents orchestrating sports analytics, live score tracking, betting market analysis, and prediction market resolution (e.g. Kalshi, Polymarket) via `mcp-server-espn`.

---

## 🎯 Core Agent Recipes & Playbooks

### 1. Prediction Market Resolution & Fair-Value Arbitrage
When resolving or handicapping sports event contracts on prediction platforms:
- **Step 1: Discover Active Game**: Call `get_scoreboard(sport="baseball", league="mlb", date="YYYYMMDD")` to locate the target `event_id`, live game status (`state: "in"`, `"pre"`, or `"post"`), and current score.
- **Step 2: Extract Consensus Odds & Metrics**: Call `get_game_summary(sport=..., league=..., event_id=...)` to retrieve consensus betting lines (`spread`, `moneyline`, `over/under` from DraftKings/Caesars/ESPN BET) and the proprietary ESPN Matchup Predictor (win probability percentage).
- **Step 3: Analyze Injury & Depth Impacts**: Cross-reference `get_team_depth_chart(team_id=...)` with active injury designations in `get_team_roster(team_id=...)` to determine if key starters (e.g., QB1, Ace Pitcher) are ruled out.
- **Step 4: Synthesize Fair-Value Estimate**: Compare the implied probability from sportsbooks against the prediction market contract price to detect mispricings.

### 2. Live In-Game Momentum & Win Probability Tracking
- **Real-Time Clocks**: Inspect `get_scoreboard(...)` to parse quarter/period, remaining time, possession, and current down/distance or inning outs.
- **Live Win Probability**: Call `get_game_summary(event_id=...)` to track the live `winprobability` curve across game progression and evaluate in-game swings.
- **Top Performers**: Call `get_player_stats(event_id=...)` to inspect live individual player boxscores (points, strikeouts, yards, touches).

### 3. Team Form, Streaks & Strength of Schedule
- **Standings & Streaks**: Invoke `get_standings(sport=..., league=...)` to evaluate conference/division ranking, differential, home/away splits, and current win/loss streaks.
- **Recent Results & Momentum**: Call `get_team_schedule(team_id=..., season=...)` to review past 5 games, opponent quality, and margin of victory.
- **National Polls**: For college sports (NCAAF / NCAAB), call `get_rankings(sport="football", league="college-football")` to check AP Top 25 and CFP committee ranks.

### 4. Player Props & Fantasy Analysis
- **Career & Season Splits**: Call `get_athlete_overview(athlete_id=...)` to fetch statistical splits (home vs. away, turf vs. grass, vs. opponent).
- **Recent Rotowire Intelligence**: Review `rotowire_notes` inside athlete overview for beat reporter injury quotes and practice status.

---

## 🛠️ Tool Suite Reference (10 Tools)

| Tool | Purpose | Annotations | Key Parameters |
| :--- | :--- | :--- | :--- |
| `get_scoreboard` | Live & historical scores, statuses, TV broadcasts, starters | `readOnlyHint=True` | `sport`, `league`, `date`, `week`, `season_type`, `limit` |
| `get_game_summary` | Consensus betting lines, predictor win %, injuries, series | `readOnlyHint=True` | `sport`, `league`, `event_id` |
| `get_player_stats` | Boxscore statistics for individual athletes across game | `readOnlyHint=True` | `sport`, `league`, `event_id` |
| `get_standings` | Division, conference, and league standings with streaks | `readOnlyHint=True` | `sport`, `league`, `season` |
| `get_news` | Headlines, breaking injury analysis, and roster moves | `readOnlyHint=True` | `sport`, `league`, `limit` |
| `get_rankings` | Top 25 national polls (AP, Coaches, CFP) for college sports | `readOnlyHint=True` | `sport`, `league` |
| `get_team_roster` | Active squad roster, jersey numbers, and injuries | `readOnlyHint=True` | `sport`, `league`, `team_id` |
| `get_team_depth_chart`| Positional starter / backup tiers (QB1, QB2, etc.) | `readOnlyHint=True` | `sport`, `league`, `team_id` |
| `get_team_schedule` | Full season schedule and past game scores for a team | `readOnlyHint=True` | `sport`, `league`, `team_id`, `season` |
| `get_athlete_overview`| Career splits, recent game logs, and fantasy notes | `readOnlyHint=True` | `sport`, `league`, `athlete_id` |

---

## ⚡ Agent Operating Guidelines

1. **Domain Shortcut & Alias Normalization**:
   Tools accept both explicit pairs (`sport="baseball", league="mlb"`) and common shorthand aliases (`sport="mlb", league="mlb"` or `sport="nfl", league="nfl"`). The server automatically normalizes them.
2. **Deterministic Caching (SEP-2549)**:
   The server implements catalog caching with `ttl_ms=3600000` (1 hr) on metadata discovery and `ttl_ms=30000` (30s) on scoreboards. Agents should avoid redundant rapid-polling within cache TTLs.
3. **Graceful Fail-Closed Handling**:
   All tools return `{ "status": "success", "data": ... }` or `{ "status": "error", "message": "[REDACTED]" }`. Agents should inspect `status` and handle errors cleanly without crashing.
