"""Teams and roster domain sub-server for ESPN MCP."""

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
from espn_mcp.middleware import TeamsDomainGuardMiddleware

logger = logging.getLogger(__name__)

teams_server = FastMCP("espn-teams")
teams_server.add_middleware(TeamsDomainGuardMiddleware())

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


@teams_server.tool(
    name="get_team_roster",
    description=(
        "Fetch active team roster and injury designations grouped by position, including jersey "
        "numbers, experience, position abbreviations, and coaching staff."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def get_team_roster(
    sport: str,
    league: str,
    team_id: str,
) -> Any:
    return await client_module.get_client().get_team_roster(
        sport=sport, league=league, team_id=team_id
    )


@teams_server.tool(
    name="get_team_depth_chart",
    description=(
        "Fetch team depth chart showing positional starter and backup hierarchies "
        "(e.g. QB1, QB2, RB1, RB2) to evaluate starting status and backup substitution impacts."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def get_team_depth_chart(
    sport: str,
    league: str,
    team_id: str,
) -> Any:
    """Fetch positional depth chart."""
    return await client_module.get_client().get_team_depth_chart(
        sport=sport, league=league, team_id=team_id
    )


@teams_server.tool(
    name="get_player_stats",
    description=(
        "Extract detailed individual player boxscores and performance metrics for a game "
        "(e.g., Strikeouts and Innings Pitched for MLB pitchers; Points, Rebounds, Assists for "
        "NBA/WNBA; Passing, Rushing, Receiving for NFL/NCAAF; Goals and Assists for Soccer/NHL)."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def get_player_stats(
    sport: str,
    league: str,
    event_id: str,
) -> Any:
    """Fetch structured player boxscore statistics."""
    return await client_module.get_client().get_player_stats(
        sport=sport, league=league, event_id=event_id
    )


@teams_server.tool(
    name="get_athlete_overview",
    description=(
        "Fetch athlete biographical information, season/career statistical splits, recent "
        "individual game logs, rotowire fantasy notes, and next upcoming match."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def get_athlete_overview(
    sport: str,
    league: str,
    athlete_id: str,
) -> Any:
    """Fetch comprehensive athlete profile and game log."""
    return await client_module.get_client().get_athlete_overview(
        sport=sport, league=league, athlete_id=athlete_id
    )


@teams_server.tool(
    name="search",
    description=(
        "Search ESPN's entity index by name for athletes, teams, or leagues. "
        "Returns IDs, display names, and metadata needed to call other tools."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def search(
    query: str,
    type: str = "player",
    limit: int = 5,
) -> Any:
    """Search athletes or teams to discover their ESPN entity IDs."""
    return await client_module.get_client().search(query=query, type=type, limit=limit)


@teams_server.tool(
    name="list_teams",
    description=(
        "Fetch all active teams in a given sport and league, returning team IDs, "
        "names, abbreviations, locations, and colors."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def list_teams(
    sport: str,
    league: str,
) -> Any:
    """List all teams for a sport and league."""
    return await client_module.get_client().list_teams(sport=sport, league=league)


@teams_server.tool(
    name="get_team",
    description=(
        "Fetch detailed information for a single team including standing summary, "
        "overall record, venue, and next scheduled event."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def get_team(
    sport: str,
    league: str,
    team_id: str,
) -> Any:
    """Fetch detailed team profile and upcoming event."""
    return await client_module.get_client().get_team(sport=sport, league=league, team_id=team_id)


@teams_server.tool(
    name="get_team_statistics",
    description=(
        "Fetch team and opponent season statistics across offensive, defensive, "
        "and special teams categories."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def get_team_statistics(
    sport: str,
    league: str,
    team_id: str,
) -> Any:
    """Fetch season team and opponent statistics."""
    return await client_module.get_client().get_team_statistics(
        sport=sport, league=league, team_id=team_id
    )


@teams_server.tool(
    name="get_athlete_bio",
    description=(
        "Fetch detailed athlete biography including birthplace, college, draft round and pick, "
        "experience, physical measurements, and background."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def get_athlete_bio(
    sport: str,
    league: str,
    athlete_id: str,
) -> Any:
    """Fetch biographical information for an athlete."""
    return await client_module.get_client().get_athlete_bio(
        sport=sport, league=league, athlete_id=athlete_id
    )


@teams_server.tool(
    name="get_athlete_stats",
    description=(
        "Fetch career and season statistical totals and averages across all standard "
        "and advanced metric categories for an athlete."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def get_athlete_stats(
    sport: str,
    league: str,
    athlete_id: str,
    season: int | None = None,
) -> Any:
    """Fetch career and season statistics for an athlete."""
    return await client_module.get_client().get_athlete_stats(
        sport=sport, league=league, athlete_id=athlete_id, season=season
    )


@teams_server.tool(
    name="get_athlete_gamelog",
    description=(
        "Fetch game-by-game statistical performance log for an athlete across an entire season."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def get_athlete_gamelog(
    sport: str,
    league: str,
    athlete_id: str,
    season: int | None = None,
) -> Any:
    """Fetch game-by-game log for an athlete."""
    return await client_module.get_client().get_athlete_gamelog(
        sport=sport, league=league, athlete_id=athlete_id, season=season
    )


@teams_server.tool(
    name="get_athlete_splits",
    description=(
        "Fetch situational split statistics for an athlete (home vs away, monthly performance, "
        "opponents, and win/loss splits)."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def get_athlete_splits(
    sport: str,
    league: str,
    athlete_id: str,
    season: int | None = None,
) -> Any:
    """Fetch situational splits for an athlete."""
    return await client_module.get_client().get_athlete_splits(
        sport=sport, league=league, athlete_id=athlete_id, season=season
    )


@teams_server.prompt("team_evaluation")
def team_evaluation_prompt(sport: str, league: str, team_id: str) -> str:
    """Generate prompt template for evaluating a team's roster, depth, form, and schedule."""
    return (
        f"Perform a deep team analysis for {sport}/{league} team '{team_id}'.\n"
        f"1. Use teams_get_team_roster to check active talent and injury designations.\n"
        f"2. Use teams_get_team_depth_chart to assess positional depth.\n"
        f"3. Use games_get_team_schedule to review recent form, strength of schedule, and momentum."
    )
