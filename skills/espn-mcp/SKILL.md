---
name: espn-mcp
description: Enterprise Agent Skill for live and historical sports analytics, odds, predictions, rosters, and prediction market resolution via mcp-server-espn.
---

# ESPN Sports Analytics MCP Server (`mcp-server-espn`) Agent Skill

This skill provides expert operating guidelines, architectural recipes, and best practices for AI agents orchestrating sports analytics, live score tracking, betting market analysis, and prediction market resolution (e.g. Kalshi, Polymarket) via `mcp-server-espn`.

---

## 🏗️ Architecture & Server Composition

The server is composed of three domain sub-servers mounted onto a root FastMCP gateway with hierarchical middleware:
- **`espn-games` (`games_*`)**: Live scores, game summaries, team schedules, standings, rankings, and `game_analysis` prompt.
- **`espn-teams` (`teams_*`)**: Team rosters, depth charts, player stats, athlete profiles, and `team_evaluation` prompt.
- **`espn-news` (`news_*`)**: League news, `espn://news/reference/supported-leagues`, and `espn://news/reference/capabilities`.

### Deployment Profiles (`--profile` / `ESPN_MCP_PROFILE`)
Domain-mount profiles:
- `full` (default): All 33 domain tools, both prompts and both resources. Tool Search and Code Mode attach only here.
- `games`: Scores, summaries, schedules, standings, rankings, transactions, league leaders, draft, live play-by-play, situations, odds, probabilities, predictor, calendar, futures, power index (20 tools).
- `teams`: Rosters, depth charts, player stats, athlete profiles, bio, stats, gamelog, splits, search, list teams, team detail, team stats (12 tools).
- `news`: News and reference resources (1 tool).
- `readonly`: Every tool annotated `readOnlyHint=True`. All 33 ESPN tools are reads, so it lists all 33; a call to any tool without the hint is refused with `isError: true`.

Job profiles (every domain mounted, tools limited to one job; prompts and resources stay):
- `gameday` (11 tools): follow today's games live with scoreboards, game situation, play-by-play, win probability, box scores and news.
- `betting` (13 tools): compare odds, futures, matchup predictions and power ratings, check injuries, and confirm final results.
- `scouting` (14 tools): evaluate athletes and rosters with depth charts, injuries, bios, stats, splits, game logs, leaderboards, transactions and the draft.
- `season` (14 tools): research a season with standings, rankings, power index, team statistics and leaders, schedules, league structure and the draft.

An unknown profile name fails at startup. Under `ESPN_MCP_READONLY=1` the `readOnlyHint` annotation alone decides what may run.

### Tool Search and Code Mode (profile `full` only)
- **Default (Flat Catalog)**: Preserves the standard flat `tools/list` schema for universal client compatibility (Claude, Cursor, Cortex, Antigravity).
- **Opt-In Tool Search**: `--enable-tool-search` or `ESPN_MCP_ENABLE_TOOL_SEARCH=1` replaces the flat list with `search_tools` and `call_tool`. Use `search_tools` first, then `call_tool(name=..., arguments=...)`. The backend is `regex` (default: `search_tools(pattern=...)`) or `bm25` (`--tool-search-backend bm25` / `ESPN_MCP_TOOL_SEARCH_BACKEND=bm25`: `search_tools(query=...)` in natural language).
- **Opt-In Code Mode (experimental)**: `--enable-code-mode` or `ESPN_MCP_ENABLE_CODE_MODE=1` exposes `search`, `get_schema` and `execute`. It needs `fastmcp[code-mode]` (the `pydantic-monty` sandbox) installed alongside the server; without it attach is skipped with a warning and the flat list stays. It cannot be combined with Tool Search, and `execute` is refused under read-only.
- On any other profile, either flag alone is ignored with a warning; enabling both raises `ValueError` on every profile.

---

## 🎯 Core Agent Recipes & Playbooks

### 1. Prediction Market Resolution & Fair-Value Arbitrage
When resolving or handicapping sports event contracts on prediction platforms:
- **Step 1: Discover Active Game**: Call `games_get_scoreboard(sport="baseball", league="mlb", date="YYYYMMDD")` to locate the target `event_id`, live game status (`state: "in"`, `"pre"`, or `"post"`), and current score.
- **Step 2: Extract Consensus Odds & Metrics**: Call `games_get_event_odds(sport=..., league=..., event_id=...)` and `games_get_game_summary(sport=..., league=..., event_id=...)` to retrieve consensus betting lines (`spread`, `moneyline`, `over/under` from DraftKings/Caesars/ESPN BET) and the proprietary ESPN Matchup Predictor (`games_get_game_predictor`).
- **Step 3: Analyze In-Game Situation & Win Probabilities**: Query `games_get_game_situation` and `games_get_win_probabilities` for granular down/distance, red zone state, and live win expectancy curve.
- **Step 4: Analyze Injury & Depth Impacts**: Cross-reference `teams_get_team_depth_chart(sport=..., league=..., team_id=...)` with active injury designations in `teams_get_team_roster(sport=..., league=..., team_id=...)` to determine if key starters (e.g., QB1, Ace Pitcher) are ruled out.
- **Step 5: Synthesize Fair-Value Estimate**: Compare the implied probability from sportsbooks against the prediction market contract price to detect mispricings.

### 2. Live In-Game Momentum & Win Probability Tracking
- **Real-Time Clocks & Ticker**: Inspect `games_get_scoreboard(...)` or `games_get_scoreboard_header(...)` to parse quarter/period, remaining time, possession, and current score.
- **Live Play-by-Play**: Call `games_get_play_by_play(sport=..., league=..., event_id=..., limit=20)` to inspect drive sequence and scoring events.
- **Live Win Probability & Situation**: Call `games_get_win_probabilities` and `games_get_game_situation` to track the live expectancy curve and active field position.
- **Top Performers**: Call `teams_get_player_stats(sport=..., league=..., event_id=...)` to inspect live individual player boxscores (points, strikeouts, yards, touches).

### 3. Team Form, Streaks & Strength of Schedule
- **Standings & Streaks**: Invoke `games_get_standings(sport=..., league=...)` to evaluate conference/division ranking, differential, home/away splits, and current win/loss streaks.
- **Power Index & Projections**: Call `games_get_power_index(sport=..., league=...)` to retrieve ESPN FPI/BPI team efficiency and power ratings.
- **Futures & Season Outrights**: Call `games_get_futures(sport=..., league=..., season=...)` to evaluate championship odds and win totals.
- **Recent Results & Momentum**: Call `games_get_team_schedule(sport=..., league=..., team_id=..., season=...)` to review past 5 games, opponent quality, and margin of victory.
- **National Polls**: For college sports (NCAAF / NCAAB), call `games_get_rankings(sport="football", league="college-football")` to check AP Top 25 and CFP committee ranks.

### 4. Player Props, Athlete Deep Dive & Fantasy Analysis
- **Career & Background**: Call `teams_get_athlete_bio(sport=..., league=..., athlete_id=...)` for draft history, college, and physical profile.
- **Career & Season Splits**: Call `teams_get_athlete_splits(sport=..., league=..., athlete_id=...)` to fetch statistical splits (home vs. away, turf vs. grass, vs. opponent).
- **Game-by-Game Trends**: Call `teams_get_athlete_gamelog(sport=..., league=..., athlete_id=...)` to inspect weekly stat trajectories.
- **Leaderboards**: Call `games_get_leaders_by_athlete(sport=..., league=...)` to compare against league leaders.
- **Recent Rotowire Intelligence**: Review `rotowire_notes` inside athlete overview for beat reporter injury quotes and practice status.

---

## 🛠️ Tool Suite Reference (33 Domain Tools)

| Domain | Tool | Purpose | Annotations | Key Parameters |
| :--- | :--- | :--- | :--- | :--- |
| **Games** | `games_get_scoreboard` | Live & historical scores, statuses, TV broadcasts, starters | `readOnlyHint=True` | `sport`, `league`, `date`, `week`, `season_type`, `limit` |
| **Games** | `games_get_game_summary` | Consensus betting lines, predictor win %, ATS, scoring plays, drives | `readOnlyHint=True` | `sport`, `league`, `event_id` |
| **Games** | `games_get_team_schedule` | Full season schedule and past game scores for a team | `readOnlyHint=True` | `sport`, `league`, `team_id`, `season` |
| **Games** | `games_get_standings` | Division, conference, and league standings with streaks | `readOnlyHint=True` | `sport`, `league`, `season` |
| **Games** | `games_get_rankings` | Top 25 national polls (AP, Coaches, CFP) for college sports | `readOnlyHint=True` | `sport`, `league` |
| **Games** | `games_get_transactions` | League transactions, roster trades, signings, releases | `readOnlyHint=True` | `sport`, `league`, `limit` |
| **Games** | `games_get_leaders_by_athlete` | Statistical leaderboards across players in a league | `readOnlyHint=True` | `sport`, `league`, `limit`, `category`, `sort` |
| **Games** | `games_get_leaders_by_team` | Team statistical rankings and leaderboards | `readOnlyHint=True` | `sport`, `league`, `limit`, `category`, `sort` |
| **Games** | `games_get_league_groups` | League conference, division, and structural group hierarchies | `readOnlyHint=True` | `sport`, `league` |
| **Games** | `games_get_league_events` | League-wide calendar of scheduled events | `readOnlyHint=True` | `sport`, `league`, `dates` |
| **Games** | `games_get_league_draft` | League draft rounds, team selections, and pick results | `readOnlyHint=True` | `sport`, `league`, `season` |
| **Games** | `games_get_scoreboard_header` | Live ticker scoreboard header data across games | `readOnlyHint=True` | `sport`, `league` |
| **Games** | `games_get_event_odds` | Sportsbook consensus and provider odds | `readOnlyHint=True` | `sport`, `league`, `event_id`, `competition_id` |
| **Games** | `games_get_play_by_play` | Play-by-play sequence, clock, scoring, and drive events | `readOnlyHint=True` | `sport`, `league`, `event_id`, `limit`, `page` |
| **Games** | `games_get_game_situation` | Real-time in-game situation (down, distance, possession) | `readOnlyHint=True` | `sport`, `league`, `event_id` |
| **Games** | `games_get_win_probabilities` | Live and historical win probability curves | `readOnlyHint=True` | `sport`, `league`, `event_id` |
| **Games** | `games_get_game_predictor` | Pre-game and in-game matchup predictor metrics | `readOnlyHint=True` | `sport`, `league`, `event_id` |
| **Games** | `games_get_calendar` | League schedule calendar and active event dates | `readOnlyHint=True` | `sport`, `league`, `dates` |
| **Games** | `games_get_futures` | Season futures betting markets (championship, win totals) | `readOnlyHint=True` | `sport`, `league`, `season` |
| **Games** | `games_get_power_index` | Team power index (FPI / BPI) ratings and efficiency | `readOnlyHint=True` | `sport`, `league`, `season` |
| **Teams** | `teams_search` | Global search for athletes and teams by keyword | `readOnlyHint=True` | `query`, `type`, `limit` |
| **Teams** | `teams_list_teams` | Complete directory of teams in a league | `readOnlyHint=True` | `sport`, `league` |
| **Teams** | `teams_get_team` | Team overview, venue, records, standing summary, next event | `readOnlyHint=True` | `sport`, `league`, `team_id` |
| **Teams** | `teams_get_team_statistics` | Team and opponent statistical splits (passing, rushing, etc.) | `readOnlyHint=True` | `sport`, `league`, `team_id` |
| **Teams** | `teams_get_team_roster` | Active squad roster, coach, jersey numbers, and injuries | `readOnlyHint=True` | `sport`, `league`, `team_id` |
| **Teams** | `teams_get_team_depth_chart`| Positional starter / backup tiers (QB1, QB2, etc.) | `readOnlyHint=True` | `sport`, `league`, `team_id` |
| **Teams** | `teams_get_player_stats` | Boxscore statistics for individual athletes across game | `readOnlyHint=True` | `sport`, `league`, `event_id` |
| **Teams** | `teams_get_athlete_overview`| Career splits, recent game logs, and fantasy notes | `readOnlyHint=True` | `sport`, `league`, `athlete_id` |
| **Teams** | `teams_get_athlete_bio` | Detailed athlete background, draft history, college | `readOnlyHint=True` | `sport`, `league`, `athlete_id` |
| **Teams** | `teams_get_athlete_stats` | Full seasonal and career category statistics | `readOnlyHint=True` | `sport`, `league`, `athlete_id`, `season` |
| **Teams** | `teams_get_athlete_gamelog` | Game-by-game performance log for an athlete | `readOnlyHint=True` | `sport`, `league`, `athlete_id`, `season` |
| **Teams** | `teams_get_athlete_splits` | Situational statistical splits (home/away, turf/grass) | `readOnlyHint=True` | `sport`, `league`, `athlete_id`, `season` |
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
