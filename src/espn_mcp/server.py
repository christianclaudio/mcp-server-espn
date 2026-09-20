"""FastMCP server instance, ESPN tool definitions, annotations, and entrypoint.

Conforms to MCP 2026-07-28 specifications.
"""

from __future__ import annotations

import argparse
import functools
import json
import logging
import signal
import sys
import traceback
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastmcp import FastMCP
from fastmcp.tools import FunctionTool
from mcp.server.caching import CacheHint
from mcp.types import ToolAnnotations

from espn_mcp import __version__
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
    "resources/templates/list": CacheHint(ttl_ms=settings.CATALOG_CACHE_TTL_MS, scope="public"),
    "server/discover": CacheHint(ttl_ms=settings.CATALOG_CACHE_TTL_MS, scope="public"),
}

client = ESPNClient()


@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Manage server lifecycle and persistent client resources."""
    logger.info("Starting up ESPN MCP server")
    try:
        yield {"client": client}
    finally:
        logger.info("Shutting down ESPN MCP resources")
        await client.close()


# Initialize FastMCP 4 server
mcp = FastMCP(
    "espn-mcp",
    version=__version__,
    lifespan=server_lifespan,
    cache_ttl=settings.CATALOG_CACHE_TTL_MS // 1000,
    cache_scope="public",
)


def _streamable_http_app(
    self: FastMCP,
    path: str | None = None,
    stateless_http: bool | None = None,
    json_response: bool | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
    **kwargs: Any,
) -> Any:
    """Compatibility bridge for streamable HTTP ASGI application."""
    allowed_hosts = kwargs.pop("allowed_hosts", None)
    if allowed_hosts is None:
        allowed_hosts = [host, "localhost", f"{host}:{port}", f"localhost:{port}"]
    return self.http_app(
        path=path,
        transport="streamable-http",
        stateless_http=stateless_http,
        json_response=json_response,
        host_origin_protection=True,
        allowed_hosts=allowed_hosts,
        **kwargs,
    )


mcp.streamable_http_app = _streamable_http_app.__get__(mcp, FastMCP)  # type: ignore[attr-defined]

if not hasattr(FunctionTool, "input_schema"):
    FunctionTool.input_schema = property(lambda self: self.parameters)  # type: ignore[attr-defined]

# MCP Behavioral Annotations
ANNOTATION_READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)


def espn_tool(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator that wraps MCP tools with structured error handling and secret redaction."""

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


@mcp.tool(
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
    return await client.get_scoreboard(
        sport=sport,
        league=league,
        dates=date,
        week=week,
        season_type=season_type,
        group=group,
        limit=limit,
    )


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
@espn_tool
async def get_game_summary(
    sport: str,
    league: str,
    event_id: str,
) -> Any:
    """Fetch complete game summary, odds, and analytics."""
    return await client.get_game_summary(sport=sport, league=league, event_id=event_id)


@mcp.tool(
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
    return await client.get_player_stats(sport=sport, league=league, event_id=event_id)


@mcp.tool(
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
    return await client.get_standings(sport=sport, league=league, season=season)


@mcp.tool(
    name="get_news",
    description=(
        "Fetch recent news headlines, injury updates, breaking analysis, and roster moves "
        "for a given sport and league."
    ),
    annotations=ANNOTATION_READ_ONLY,
)
@espn_tool
async def get_news(
    sport: str,
    league: str,
    limit: int = 10,
) -> Any:
    """Fetch latest sport and league news."""
    return await client.get_news(sport=sport, league=league, limit=limit)


@mcp.tool(
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
    return await client.get_rankings(sport=sport, league=league)


@mcp.tool(
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
    """Fetch active team roster and squad information."""
    return await client.get_team_roster(sport=sport, league=league, team_id=team_id)


@mcp.tool(
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
    return await client.get_team_depth_chart(sport=sport, league=league, team_id=team_id)


@mcp.tool(
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
    return await client.get_team_schedule(
        sport=sport, league=league, team_id=team_id, season=season
    )


@mcp.tool(
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
    return await client.get_athlete_overview(sport=sport, league=league, athlete_id=athlete_id)


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
    """Handle SIGTERM/SIGINT from host supervisor and unwind gracefully."""
    logger.info("Received signal %s; shutting down.", signum)
    sys.exit(0)


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
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host address for HTTP transports (default: 127.0.0.1).",
    )
    parser.add_argument("--port", type=int, default=8000, help="Port for HTTP transports.")
    parser.add_argument(
        "--stateless",
        action=argparse.BooleanOptionalAction,
        default=settings.MCP_STATELESS_HTTP,
        help=(
            "Run Streamable HTTP in stateless mode "
            "(fresh connection per request, no Mcp-Session-Id)."
        ),
    )
    parser.add_argument(
        "--json-response",
        action=argparse.BooleanOptionalAction,
        default=settings.MCP_JSON_RESPONSE,
        help="Return direct JSON responses instead of SSE text/event-stream over Streamable HTTP.",
    )
    parser.add_argument(
        "--allowed-host",
        action="append",
        dest="allowed_hosts",
        default=None,
        help="Allowed host for HTTP transports (can be specified multiple times).",
    )
    parser.add_argument(
        "--allowed-origin",
        action="append",
        dest="allowed_origins",
        default=None,
        help="Allowed origin for HTTP transports (can be specified multiple times).",
    )
    args = parser.parse_args()

    if args.transport != "streamable-http":
        if args.stateless:
            logger.warning("--stateless flag is only applicable to 'streamable-http' transport.")
        if args.json_response:
            logger.warning(
                "--json-response flag is only applicable to 'streamable-http' transport."
            )

    hosts = (
        args.allowed_hosts
        if args.allowed_hosts is not None
        else [args.host, "localhost", f"{args.host}:{args.port}", f"localhost:{args.port}"]
    )
    run_kwargs: dict[str, Any] = {
        "host": args.host,
        "port": args.port,
        "host_origin_protection": True,
        "allowed_hosts": hosts,
    }
    if args.allowed_origins is not None:
        run_kwargs["allowed_origins"] = args.allowed_origins

    if args.transport == "sse":
        logger.warning(
            "Deprecation Warning: HTTP+SSE transport is deprecated per MCP 2026-07-28 spec "
            "(SEP-2577). Please migrate to Streamable HTTP (--transport streamable-http)."
        )
        mcp.run(
            transport="sse",
            **run_kwargs,
        )
    elif args.transport == "streamable-http":
        mcp.run(
            transport="streamable-http",
            stateless_http=args.stateless,
            json_response=args.json_response,
            **run_kwargs,
        )
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
