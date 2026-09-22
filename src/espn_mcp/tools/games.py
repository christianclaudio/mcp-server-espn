"""Games and scores domain sub-server for ESPN MCP."""

from __future__ import annotations

import functools
import logging
import traceback
from collections.abc import Callable
from typing import Any

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

import espn_mcp.client as client_module
from espn_mcp.errors import redact_secrets
from espn_mcp.middleware import GamesDomainGuardMiddleware

logger = logging.getLogger(__name__)

games_server = FastMCP("espn-games")
games_server.add_middleware(GamesDomainGuardMiddleware())

ANNOTATION_READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)


def espn_tool(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator wrapping tools with structured error handling and secret redaction."""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            data = await fn(*args, **kwargs)
            return {"status": "success", "data": data}
        except Exception as exc:
            logger.error(
                "Error executing %s: %s",
                fn.__name__,
                redact_secrets(traceback.format_exc()),
            )
            return {"status": "error", "message": redact_secrets(str(exc))}

    return wrapper


@games_server.tool(
    name="get_scoreboard",
    description=(
        "Fetch live scores, game status, periods/innings, clocks/outs, TV broadcasts, and "
        "probable starters (e.g. starting pitchers or quarterbacks) for a sport and league. "
        "Supports filtering by date (YYYYMMDD), week number, season type, and Top 25 groups."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def get_scoreboard(
    sport: str,
    league: str,
    date: str | None = None,
    week: int | None = None,
    season_type: int | None = None,
    group: str | None = None,
    limit: int = 50,
) -> Any:
    """Fetch live and historical scoreboards."""
    return await client_module.get_client().get_scoreboard(
        sport=sport,
        league=league,
        dates=date,
        week=week,
        season_type=season_type,
        group=group,
        limit=limit,
    )


@games_server.tool(
    name="get_game_summary",
    description=(
        "Fetch comprehensive game summary for an event ID, including consensus betting lines "
        "(spread, moneyline, over/under from DraftKings/Caesars/ESPN BET), matchup predictor "
        "(FPI/BPI win probabilities), live win probability curve, season head-to-head series, "
        "last 5 games momentum, team statistics, and in-game injuries."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def get_game_summary(
    sport: str,
    league: str,
    event_id: str,
) -> Any:
    return await client_module.get_client().get_game_summary(
        sport=sport, league=league, event_id=event_id
    )


@games_server.tool(
    name="get_team_schedule",
    description=(
        "Fetch full season schedule and historical game results for a specific team, including "
        "opponents, scores, dates, home/away status, and event IDs."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def get_team_schedule(
    sport: str,
    league: str,
    team_id: str,
    season: int | None = None,
) -> Any:
    """Fetch complete team season schedule and past game scores."""
    return await client_module.get_client().get_team_schedule(
        sport=sport, league=league, team_id=team_id, season=season
    )


@games_server.tool(
    name="get_standings",
    description=(
        "Fetch current or historical division, conference, and overall league standings, "
        "including win-loss records, win percentages, games back, streaks, and differential."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def get_standings(
    sport: str,
    league: str,
    season: int | None = None,
) -> Any:
    """Fetch division and conference standings."""
    return await client_module.get_client().get_standings(sport=sport, league=league, season=season)


@games_server.tool(
    name="get_rankings",
    description=(
        "Fetch Top 25 national polls and rankings (AP Top 25, Coaches Poll, College Football "
        "Playoff rankings) for college sports like NCAAF and NCAAB, including current and previous "
        "ranks, votes, and records."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def get_rankings(
    sport: str,
    league: str,
) -> Any:
    """Fetch national rankings and polls."""
    return await client_module.get_client().get_rankings(sport=sport, league=league)


@games_server.prompt("game_analysis")
def game_analysis_prompt(sport: str, league: str, event_id: str) -> str:
    """Generate prompt template for performing comprehensive sports market and matchup analysis."""
    return (
        f"Perform a comprehensive prediction market and odds analysis for "
        f"{sport}/{league} game '{event_id}'.\n"
        f"1. Use games_get_game_summary(sport='{sport}', league='{league}', event_id='{event_id}') "
        f"to inspect betting lines, predictor projections, and injuries.\n"
        f"2. Use teams_get_player_stats(sport='{sport}', league='{league}', event_id='{event_id}') "
        f"to evaluate key performers.\n"
        f"3. Synthesize findings into fair-value probability estimates."
    )
