"""Shared tool parameter types with descriptions for ESPN MCP tools.

The descriptions land in each tool's input schema, which clients show to the model and
which FastMCP's Tool Search indexes along with the tool name and description. Every
tools/list carries them, so each stays one short line; ESPN's season rule is written out
in full once, on ``games_get_standings``, and the other season parameters point to it.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

Sport = Annotated[
    str,
    Field(description="e.g. basketball"),
]

League = Annotated[
    str,
    Field(description="e.g. nba, eng.1"),
]

# ESPN's season convention, checked live on standings, team schedules, athlete game logs
# and splits, futures, and power index. Written out in full only on games_get_standings.
SEASON_CONVENTION = (
    "Season by the year it ends, e.g. 2016 for the 2015-16 NBA or NHL season; NFL and MLB: "
    "the year played; soccer: the year it starts, e.g. 2026 for the 2026-27 Premier League."
)

# One-line pointer used by every other season parameter.
SEASON_SEE_STANDINGS = "Season year; see games_get_standings for the convention."

StandingsSeason = Annotated[
    int | None,
    Field(description=f"{SEASON_CONVENTION} Omit for the current season."),
]

Season = Annotated[
    int | None,
    Field(description=SEASON_SEE_STANDINGS),
]

AthleteSeason = Annotated[
    int | None,
    Field(description=SEASON_SEE_STANDINGS),
]

MarketSeason = Annotated[
    int | None,
    Field(description=f"{SEASON_SEE_STANDINGS} Omit for ESPN's current."),
]

AthleteStatsSeason = Annotated[
    int | None,
    Field(description="Echoed only; ESPN returns every season's totals."),
]

DraftSeason = Annotated[
    int | None,
    Field(description="Draft year (the year it's held). Omit for ESPN's current."),
]
