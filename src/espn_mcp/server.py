"""MCPServer instance, ESPN tool definitions, annotations, and entrypoint.

Conforms to MCP 2026-07-28 specifications.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
from typing import Any, Literal

from mcp.server import CacheHint, MCPServer
from mcp.types import ToolAnnotations

from espn_mcp.client import SPORT_LEAGUE_MAP, ESPNClient
from espn_mcp.config import settings
from espn_mcp.errors import redact_secrets

CacheableMethod = Literal[
    "prompts/list",
    "resources/list",
    "resources/read",
    "resources/templates/list",
    "server/discover",
    "tools/list",
]

logger = logging.getLogger(__name__)

# MCP 2026-07-28 Deterministic Caching Hints (SEP-2549)
CACHE_HINTS: dict[CacheableMethod, CacheHint] = {
    "tools/list": CacheHint(ttl_ms=settings.CATALOG_CACHE_TTL_MS, scope="public"),
    "prompts/list": CacheHint(ttl_ms=settings.CATALOG_CACHE_TTL_MS, scope="public"),
    "resources/list": CacheHint(ttl_ms=settings.CATALOG_CACHE_TTL_MS, scope="public"),
    "server/discover": CacheHint(ttl_ms=settings.CATALOG_CACHE_TTL_MS, scope="public"),
}

# Initialize server
mcp = MCPServer(
    "espn-mcp",
    version="1.0.0",
    cache_hints=CACHE_HINTS,
)
client = ESPNClient()

# MCP Behavioral Annotations
ANNOTATION_READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)


@mcp.tool(
    name="get_scoreboard",
    description=(
        "Fetch live scores, game status, periods/innings, clocks/outs, TV broadcasts, and "
        "probable starters (e.g. starting pitchers or quarterbacks) for a sport and league. "
        "Supports filtering by date (YYYYMMDD), week number, season type, and Top 25 groups."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
async def get_scoreboard(
    sport: str,
    league: str,
    date: str | None = None,
    week: int | None = None,
    season_type: int | None = None,
    group: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Fetch live and historical scoreboards."""
    try:
        data = await client.get_scoreboard(
            sport=sport,
            league=league,
            dates=date,
            week=week,
            season_type=season_type,
            group=group,
            limit=limit,
        )
        return {"status": "success", "data": data}
    except Exception as exc:
        return {"status": "error", "message": redact_secrets(str(exc))}


@mcp.tool(
    name="get_game_summary",
    description=(
        "Fetch comprehensive game summary for an event ID, including consensus betting lines "
        "(spread, moneyline, over/under from DraftKings/Caesars/ESPN BET), matchup predictor "
        "(FPI/BPI win probabilities), live win probability curve, season head-to-head series, "
        "last 5 games momentum, team statistics, and in-game injuries."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
async def get_game_summary(
    sport: str,
    league: str,
    event_id: str,
) -> dict[str, Any]:
    """Fetch complete game summary, odds, and analytics."""
    try:
        data = await client.get_game_summary(sport=sport, league=league, event_id=event_id)
        return {"status": "success", "data": data}
    except Exception as exc:
        return {"status": "error", "message": redact_secrets(str(exc))}


@mcp.tool(
    name="get_player_stats",
    description=(
        "Extract detailed individual player boxscores and performance metrics for a game "
        "(e.g., Strikeouts and Innings Pitched for MLB pitchers; Points, Rebounds, Assists for "
        "NBA/WNBA; Passing, Rushing, Receiving for NFL/NCAAF; Goals and Assists for Soccer/NHL)."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
async def get_player_stats(
    sport: str,
    league: str,
    event_id: str,
) -> dict[str, Any]:
    """Fetch structured player boxscore statistics."""
    try:
        data = await client.get_player_stats(sport=sport, league=league, event_id=event_id)
        return {"status": "success", "data": data}
    except Exception as exc:
        return {"status": "error", "message": redact_secrets(str(exc))}


@mcp.tool(
    name="get_standings",
    description=(
        "Fetch current or historical division, conference, and overall league standings, "
        "including win-loss records, win percentages, games back, streaks, and differential."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
async def get_standings(
    sport: str,
    league: str,
    season: int | None = None,
) -> dict[str, Any]:
    """Fetch division and conference standings."""
    try:
        data = await client.get_standings(sport=sport, league=league, season=season)
        return {"status": "success", "data": data}
    except Exception as exc:
        return {"status": "error", "message": redact_secrets(str(exc))}


@mcp.tool(
    name="get_news",
    description=(
        "Fetch recent news headlines, injury updates, breaking analysis, and roster moves "
        "for a given sport and league."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
async def get_news(
    sport: str,
    league: str,
    limit: int = 10,
) -> dict[str, Any]:
    """Fetch latest sport and league news."""
    try:
        data = await client.get_news(sport=sport, league=league, limit=limit)
        return {"status": "success", "data": data}
    except Exception as exc:
        return {"status": "error", "message": redact_secrets(str(exc))}


@mcp.tool(
    name="get_rankings",
    description=(
        "Fetch Top 25 national polls and rankings (AP Top 25, Coaches Poll, College Football "
        "Playoff rankings) for college sports like NCAAF and NCAAB, including current and previous "
        "ranks, votes, and records."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
async def get_rankings(
    sport: str,
    league: str,
) -> dict[str, Any]:
    """Fetch national rankings and polls."""
    try:
        data = await client.get_rankings(sport=sport, league=league)
        return {"status": "success", "data": data}
    except Exception as exc:
        return {"status": "error", "message": redact_secrets(str(exc))}


@mcp.tool(
    name="get_team_roster",
    description=(
        "Fetch active team roster and injury designations grouped by position, including jersey "
        "numbers, experience, position abbreviations, and coaching staff."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
async def get_team_roster(
    sport: str,
    league: str,
    team_id: str,
) -> dict[str, Any]:
    """Fetch active team roster and squad information."""
    try:
        data = await client.get_team_roster(sport=sport, league=league, team_id=team_id)
        return {"status": "success", "data": data}
    except Exception as exc:
        return {"status": "error", "message": redact_secrets(str(exc))}


@mcp.tool(
    name="get_team_depth_chart",
    description=(
        "Fetch team depth chart showing positional starter and backup hierarchies "
        "(e.g. QB1, QB2, RB1, RB2) to evaluate starting status and backup substitution impacts."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
async def get_team_depth_chart(
    sport: str,
    league: str,
    team_id: str,
) -> dict[str, Any]:
    """Fetch positional depth chart."""
    try:
        data = await client.get_team_depth_chart(sport=sport, league=league, team_id=team_id)
        return {"status": "success", "data": data}
    except Exception as exc:
        return {"status": "error", "message": redact_secrets(str(exc))}


@mcp.tool(
    name="get_team_schedule",
    description=(
        "Fetch full season schedule and historical game results for a specific team, including "
        "opponents, scores, dates, home/away status, and event IDs."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
async def get_team_schedule(
    sport: str,
    league: str,
    team_id: str,
    season: int | None = None,
) -> dict[str, Any]:
    """Fetch complete team season schedule and past game scores."""
    try:
        data = await client.get_team_schedule(
            sport=sport, league=league, team_id=team_id, season=season
        )
        return {"status": "success", "data": data}
    except Exception as exc:
        return {"status": "error", "message": redact_secrets(str(exc))}


@mcp.tool(
    name="get_athlete_overview",
    description=(
        "Fetch athlete biographical information, season/career statistical splits, recent "
        "individual game logs, rotowire fantasy notes, and next upcoming match."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
async def get_athlete_overview(
    sport: str,
    league: str,
    athlete_id: str,
) -> dict[str, Any]:
    """Fetch comprehensive athlete profile and game log."""
    try:
        data = await client.get_athlete_overview(sport=sport, league=league, athlete_id=athlete_id)
        return {"status": "success", "data": data}
    except Exception as exc:
        return {"status": "error", "message": redact_secrets(str(exc))}


# =============================================================================
# Resources and Prompts
# =============================================================================


@mcp.resource("espn://reference/supported-leagues")
def get_supported_leagues() -> str:
    """Return JSON mapping of all supported sports, leagues, and aliases."""
    return json.dumps(SPORT_LEAGUE_MAP, indent=2)


@mcp.resource("espn://reference/capabilities")
def get_capabilities() -> str:
    """Return static system capabilities reference."""
    return (
        "ESPN MCP Server Capabilities: Scoreboards, Live Summaries, PickCenter Betting Odds, "
        "Predictor Win Probs, Player Boxscores, Standings, Rankings/Polls, Rosters, Depth Charts, "
        "Schedules, and Athlete Game Logs."
    )


@mcp.prompt("game_analysis")
def game_analysis_prompt(sport: str, league: str, event_id: str) -> str:
    """Generate prompt template for performing comprehensive sports market and matchup analysis."""
    return (
        f"Perform a comprehensive prediction market and odds analysis for "
        f"{sport}/{league} game '{event_id}'.\n"
        f"1. Use get_game_summary(sport='{sport}', league='{league}', event_id='{event_id}') "
        f"to inspect betting lines, predictor projections, and injuries.\n"
        f"2. Use get_player_stats(sport='{sport}', league='{league}', event_id='{event_id}') "
        f"to evaluate key performers.\n"
        f"3. Synthesize findings into fair-value probability estimates."
    )


@mcp.prompt("team_evaluation")
def team_evaluation_prompt(sport: str, league: str, team_id: str) -> str:
    """Generate prompt template for evaluating a team's roster, depth, form, and schedule."""
    return (
        f"Perform a deep team analysis for {sport}/{league} team '{team_id}'.\n"
        f"1. Use get_team_roster to check active talent and injury designations.\n"
        f"2. Use get_team_depth_chart to assess positional depth.\n"
        f"3. Use get_team_schedule to review recent form, strength of schedule, and momentum."
    )


def _handle_shutdown(signum: int, frame: Any) -> None:
    """Gracefully handle SIGTERM/SIGINT from host supervisor to exit with status 0 immediately."""
    os._exit(0)


def main() -> None:
    """Run MCPServer with transport selection and graceful shutdown handling."""
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    parser = argparse.ArgumentParser(description="ESPN MCP Server (2026-07-28 Spec)")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http", "sse"],
        default="stdio",
        help="Transport protocol: 'stdio' (default), 'streamable-http' (modern), or 'sse'.",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Host address for HTTP transports.")
    parser.add_argument("--port", type=int, default=8000, help="Port for HTTP transports.")
    args = parser.parse_args()

    if args.transport == "sse":
        logger.warning(
            "Deprecation Warning: HTTP+SSE transport is deprecated per MCP 2026-07-28 spec "
            "(SEP-2577). Please migrate to Streamable HTTP (--transport streamable-http)."
        )
        mcp.run(transport="sse", host=args.host, port=args.port)
    elif args.transport == "streamable-http":
        mcp.run(transport="streamable-http", host=args.host, port=args.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
