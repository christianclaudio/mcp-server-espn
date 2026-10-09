"""News and media domain sub-server for ESPN MCP."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

import espn_mcp.client as client_module
from espn_mcp.client import SPORT_LEAGUE_MAP
from espn_mcp.errors import espn_tool

logger = logging.getLogger(__name__)

news_server = FastMCP("espn-news")

ANNOTATION_READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)


@news_server.tool(
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
    return await client_module.get_client().get_news(sport=sport, league=league, limit=limit)


@news_server.resource("espn://reference/supported-leagues")
def get_supported_leagues() -> str:
    """Return JSON mapping of all supported sports, leagues, and aliases."""
    return json.dumps(SPORT_LEAGUE_MAP, indent=2)


@news_server.resource("espn://reference/capabilities")
def get_capabilities() -> str:
    """Return static system capabilities reference."""
    return (
        "ESPN MCP Server Capabilities: Scoreboards, Live Summaries, PickCenter Betting Odds, "
        "Predictor Win Probs, Player Boxscores, Standings, Rankings/Polls, Rosters, Depth Charts, "
        "Schedules, and Athlete Game Logs."
    )
