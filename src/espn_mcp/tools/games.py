"""Games and scores domain sub-server for ESPN MCP."""

from __future__ import annotations

import functools
import logging
import traceback
from collections.abc import Callable
from typing import Annotated, Any

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

import espn_mcp.client as client_module
from espn_mcp.errors import redact_secrets, tool_error
from espn_mcp.middleware import GamesDomainGuardMiddleware
from espn_mcp.tools.params import (
    AwardSeason,
    BetKind,
    DraftSeason,
    League,
    MarketSeason,
    PageIndex,
    PageLimit,
    Season,
    Sport,
    StandingsSeason,
)

_UNOFFICIAL = "Unofficial: ESPN publishes no docs for this route."

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
            failure = tool_error(exc)
        raise failure from None

    return wrapper


@games_server.tool(
    name="get_scoreboard",
    description=(
        "Fetch final scores and results for any date, including postseason games such as the "
        "Super Bowl, World Series, or NBA Finals, plus live scores, game status, "
        "periods/innings, clocks/outs, TV broadcasts, and probable starters. Returns each "
        "game's event ID. Filter by date, week, season type, or Top 25 group. Sports: football, "
        "basketball, baseball, hockey, soccer, golf, mma. Use this first to check whether a "
        "team played on a particular date; provide the date, sport, and league, and no team ID "
        "is needed. Both sport and league are required; for NFL, use sport='football' and "
        "league='nfl'. Results are capped by limit (default 50); "
        "raise limit on busy dates. For Division I men's college basketball, pass "
        "group='50'. Before concluding that a team did not play, check the team's schedule."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def get_scoreboard(
    sport: Sport,
    league: League,
    date: Annotated[
        str | None,
        Field(
            description=(
                "YYYYMMDD, e.g. 20161102; omit for today. Or an inclusive range "
                "YYYYMMDD-YYYYMMDD, e.g. 20260919-20260925 (up to 31 days; golf: any length). "
                "Outside golf a range is fetched day by day: limit applies to each day, so "
                "up to 31 times limit events, and one failed day fails the whole call."
            )
        ),
    ] = None,
    week: int | None = None,
    season_type: int | None = None,
    group: str | None = None,
    limit: int = 50,
) -> Any:
    """Fetch games on a date or for a league; use a dated scoreboard to check if a team played."""
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
    sport: Sport,
    league: League,
    event_id: str,
) -> Any:
    return await client_module.get_client().get_game_summary(
        sport=sport, league=league, event_id=event_id
    )


@games_server.tool(
    name="get_team_schedule",
    description=(
        "Fetch a team's full season schedule, including past results, upcoming opponents, scores, "
        "dates, home/away, and event IDs. Use this for a team's season, last game, or next game; "
        "use the scoreboard for games on a particular date. Regular season by default; "
        "season_type=3 for postseason."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def get_team_schedule(
    sport: Sport,
    league: League,
    team_id: str,
    season: Season = None,
    season_type: Annotated[
        int | None,
        Field(description="2 or omit: regular season; 3: postseason."),
    ] = None,
) -> Any:
    """Fetch complete team season schedule and past game scores."""
    return await client_module.get_client().get_team_schedule(
        sport=sport, league=league, team_id=team_id, season=season, season_type=season_type
    )


@games_server.tool(
    name="get_standings",
    description=(
        "Fetch current or historical regular-season division, conference, and league standings "
        "(e.g. which team had the best record): win-loss records, win percentages, games back, "
        "streaks, and differential. Echoes the season ESPN returned."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def get_standings(
    sport: Sport,
    league: League,
    season: StandingsSeason = None,
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
    sport: Sport,
    league: League,
) -> Any:
    """Fetch national rankings and polls."""
    return await client_module.get_client().get_rankings(sport=sport, league=league)


@games_server.tool(
    name="get_transactions",
    description=(
        "Fetch recent league player transactions (trades, free agent signings, "
        "waiver claims, roster activations, and injury reserve designations)."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def get_transactions(
    sport: Sport,
    league: League,
    limit: int = 25,
) -> Any:
    """Fetch recent league player transactions."""
    return await client_module.get_client().get_transactions(
        sport=sport, league=league, limit=limit
    )


@games_server.tool(
    name="get_leaders_by_athlete",
    description=(
        "Fetch statistical leaderboards across a league for individual athletes "
        "(e.g. passing yards, rushing, points, strikeouts)."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def get_leaders_by_athlete(
    sport: Sport,
    league: League,
    limit: int = 10,
    category: str | None = None,
    sort: str | None = None,
) -> Any:
    """Fetch league athlete statistical leaders."""
    return await client_module.get_client().get_leaders_by_athlete(
        sport=sport, league=league, limit=limit, category=category, sort=sort
    )


@games_server.tool(
    name="get_leaders_by_team",
    description=(
        "Fetch team statistical leaderboards across a league (e.g. total offense, "
        "defensive points allowed, efficiency)."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def get_leaders_by_team(
    sport: Sport,
    league: League,
    limit: int = 10,
    category: str | None = None,
    sort: str | None = None,
) -> Any:
    """Fetch league team statistical leaders."""
    return await client_module.get_client().get_leaders_by_team(
        sport=sport, league=league, limit=limit, category=category, sort=sort
    )


@games_server.tool(
    name="get_league_groups",
    description=("Fetch league conference, division, and structural group hierarchies."),
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def get_league_groups(
    sport: Sport,
    league: League,
) -> Any:
    """Fetch league conferences and divisions."""
    return await client_module.get_client().get_league_groups(sport=sport, league=league)


@games_server.tool(
    name="get_league_events",
    description=(
        "Fetch the league's current scheduled events. Date filtering is not supported here: "
        "for games on a date or range, use games_get_scoreboard with date."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def get_league_events(
    sport: Sport,
    league: League,
    dates: Annotated[
        str | None,
        Field(
            description=(
                "Omit for the league's current events. Date filtering is not supported: a "
                "valid date (YYYYMMDD) or range (YYYYMMDD-YYYYMMDD) returns an error, with "
                "no request, that points to games_get_scoreboard with the same value as date."
            )
        ),
    ] = None,
) -> Any:
    """Fetch scheduled events across a league."""
    return await client_module.get_client().get_league_events(
        sport=sport, league=league, dates=dates
    )


@games_server.tool(
    name="get_league_draft",
    description=(
        "Fetch league draft rounds, team selections, and pick results for a draft year "
        "(e.g. the first overall pick)."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def get_league_draft(
    sport: Sport,
    league: League,
    season: DraftSeason = None,
) -> Any:
    """Fetch league draft results."""
    return await client_module.get_client().get_league_draft(
        sport=sport, league=league, season=season
    )


@games_server.tool(
    name="get_scoreboard_header",
    description=("Fetch live ticker scoreboard header data across games for a sport and league."),
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def get_scoreboard_header(
    sport: Sport = "football",
    league: League = "nfl",
) -> Any:
    """Fetch live scoreboard header data."""
    return await client_module.get_client().get_scoreboard_header(sport=sport, league=league)


@games_server.tool(
    name="get_event_odds",
    description=(
        "Fetch sports betting odds, point spreads, over/under, and moneylines "
        "across sportsbooks via ESPN Core API."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def get_event_odds(
    sport: Sport,
    league: League,
    event_id: str,
    competition_id: str | None = None,
) -> Any:
    """Fetch game betting odds and lines."""
    return await client_module.get_client().get_event_odds(
        sport=sport, league=league, event_id=event_id, competition_id=competition_id
    )


@games_server.tool(
    name="get_play_by_play",
    description=(
        "Fetch granular play-by-play sequence with clock, downs, distances, yardage, "
        "and scoring flags via ESPN Core API."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def get_play_by_play(
    sport: Sport,
    league: League,
    event_id: str,
    competition_id: str | None = None,
    limit: int = 50,
    page: int = 1,
) -> Any:
    """Fetch granular game play-by-play sequence."""
    return await client_module.get_client().get_play_by_play(
        sport=sport,
        league=league,
        event_id=event_id,
        competition_id=competition_id,
        limit=limit,
        page=page,
    )


@games_server.tool(
    name="get_game_situation",
    description=(
        "Fetch real-time game situation (down, distance, yardline, possession, red zone, clock) "
        "via ESPN Core API."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def get_game_situation(
    sport: Sport,
    league: League,
    event_id: str,
    competition_id: str | None = None,
) -> Any:
    """Fetch live game situation state."""
    return await client_module.get_client().get_game_situation(
        sport=sport, league=league, event_id=event_id, competition_id=competition_id
    )


@games_server.tool(
    name="get_win_probabilities",
    description=(
        "Fetch high-density win probability timeline curve samples across an entire game."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def get_win_probabilities(
    sport: Sport,
    league: League,
    event_id: str,
    competition_id: str | None = None,
    limit: int = 50,
) -> Any:
    """Fetch win probability progression samples."""
    return await client_module.get_client().get_win_probabilities(
        sport=sport,
        league=league,
        event_id=event_id,
        competition_id=competition_id,
        limit=limit,
    )


@games_server.tool(
    name="get_game_predictor",
    description=(
        "Fetch ESPN predictive matchup model win percentages, projected margins, and ratings."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def get_game_predictor(
    sport: Sport,
    league: League,
    event_id: str,
    competition_id: str | None = None,
) -> Any:
    """Fetch predictive matchup model results."""
    return await client_module.get_client().get_game_predictor(
        sport=sport, league=league, event_id=event_id, competition_id=competition_id
    )


@games_server.tool(
    name="get_calendar",
    description=("Fetch league schedule calendar and active event dates across a season."),
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def get_calendar(
    sport: Sport,
    league: League,
    dates: str | None = None,
) -> Any:
    """Fetch season calendar and scheduled competition dates."""
    return await client_module.get_client().get_calendar(sport=sport, league=league, dates=dates)


@games_server.tool(
    name="get_futures",
    description=(
        "Fetch season futures betting markets "
        "(championship odds, conference champions, win totals)."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def get_futures(
    sport: Sport,
    league: League,
    season: MarketSeason = None,
) -> Any:
    """Fetch season futures betting markets."""
    return await client_module.get_client().get_futures(sport=sport, league=league, season=season)


@games_server.tool(
    name="get_power_index",
    description=("Fetch league team power index (FPI / BPI) ratings and efficiency metrics."),
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def get_power_index(
    sport: Sport,
    league: League,
    season: MarketSeason = None,
) -> Any:
    """Fetch league power index ratings."""
    return await client_module.get_client().get_power_index(
        sport=sport, league=league, season=season
    )


@games_server.tool(
    name="get_league_injuries",
    description=(
        f"{_UNOFFICIAL} Paged league injury report (status, comment, and detail). "
        "There is no server-side team filter."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def get_league_injuries(
    sport: Sport,
    league: League,
    page: PageIndex = 1,
    limit: PageLimit = 25,
) -> Any:
    """Fetch one page of league injuries."""
    return await client_module.get_client().get_league_injuries(
        sport=sport, league=league, page=page, limit=limit
    )


@games_server.tool(
    name="get_drives",
    description=(
        f"{_UNOFFICIAL} NFL and college football only. Drives for one competition "
        "(plays, yards, and result). competition_id defaults to event_id."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def get_drives(
    sport: Sport,
    league: League,
    event_id: str,
    competition_id: str | None = None,
    page: PageIndex = 1,
    limit: PageLimit = 50,
) -> Any:
    """Fetch drives for one competition."""
    return await client_module.get_client().get_drives(
        sport=sport,
        league=league,
        event_id=event_id,
        competition_id=competition_id,
        page=page,
        limit=limit,
    )


@games_server.tool(
    name="get_drive_plays",
    description=(
        f"{_UNOFFICIAL} NFL and college football only. Plays inside one drive. "
        "competition_id defaults to event_id."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def get_drive_plays(
    sport: Sport,
    league: League,
    event_id: str,
    drive_id: str,
    competition_id: str | None = None,
    page: PageIndex = 1,
    limit: PageLimit = 50,
) -> Any:
    """Fetch the plays inside one drive."""
    return await client_module.get_client().get_drive_plays(
        sport=sport,
        league=league,
        event_id=event_id,
        drive_id=drive_id,
        competition_id=competition_id,
        page=page,
        limit=limit,
    )


@games_server.tool(
    name="get_qbr",
    description=(
        f"{_UNOFFICIAL} NFL and college football only. League QBR, one season, or one "
        "week when week is set (season type defaults to 2, split to 0)."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def get_qbr(
    sport: Sport,
    league: League,
    season: MarketSeason = None,
    season_type: int = 2,
    week: int | None = None,
    split: int = 0,
    page: PageIndex = 1,
    limit: PageLimit = 25,
) -> Any:
    """Fetch quarterback rating for a league, season, or week."""
    return await client_module.get_client().get_qbr(
        sport=sport,
        league=league,
        season=season,
        season_type=season_type,
        week=week,
        split=split,
        page=page,
        limit=limit,
    )


@games_server.tool(
    name="list_golf_tournaments",
    description=(
        f"{_UNOFFICIAL} Golf only. Tournaments for one golf league (pga, lpga, and others)."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def list_golf_tournaments(league: League) -> Any:
    """List tournaments for a golf league."""
    return await client_module.get_client().list_golf_tournaments(league=league)


@games_server.tool(
    name="get_golf_tournament",
    description=f"{_UNOFFICIAL} Golf only. One golf tournament by id.",
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def get_golf_tournament(league: League, tournament_id: str) -> Any:
    """Fetch one golf tournament."""
    return await client_module.get_client().get_golf_tournament(
        league=league, tournament_id=tournament_id
    )


@games_server.tool(
    name="get_bets",
    description=(
        f"{_UNOFFICIAL} Flag: betting content; US availability varies. "
        "Can return no items outside betting windows. "
        "kind is featured or trending, and the result is trimmed to limit."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def get_bets(kind: BetKind = "trending", limit: PageLimit = 25) -> Any:
    """Fetch featured or trending bets."""
    return await client_module.get_client().get_bets(kind=kind, limit=limit)


@games_server.tool(
    name="get_bet_type",
    description=f"{_UNOFFICIAL} One bet type, such as spread, total, or moneyline.",
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def get_bet_type(sport: Sport, league: League, bet_type_id: str) -> Any:
    """Fetch one bet type."""
    return await client_module.get_client().get_bet_type(
        sport=sport, league=league, bet_type_id=bet_type_id
    )


@games_server.tool(
    name="get_awards",
    description=(
        f"{_UNOFFICIAL} Season awards as id and reference rows. "
        "Omit season for ESPN's current season."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def get_awards(
    sport: Sport,
    league: League,
    season: MarketSeason = None,
    page: PageIndex = 1,
    limit: PageLimit = 25,
) -> Any:
    """List season awards."""
    return await client_module.get_client().get_awards(
        sport=sport, league=league, season=season, page=page, limit=limit
    )


@games_server.tool(
    name="get_award",
    description=f"{_UNOFFICIAL} One season award and its winners.",
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def get_award(sport: Sport, league: League, season: AwardSeason, award_id: str) -> Any:
    """Fetch one season award."""
    return await client_module.get_client().get_award(
        sport=sport, league=league, season=season, award_id=award_id
    )


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
