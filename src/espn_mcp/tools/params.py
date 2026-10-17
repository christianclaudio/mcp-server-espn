"""Shared tool parameter types with descriptions for ESPN MCP tools.

The descriptions land in each tool's input schema, which clients show to the model and
which FastMCP's Tool Search indexes along with the tool name and description.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

Sport = Annotated[
    str,
    Field(
        description=(
            "ESPN sport: football, basketball, baseball, hockey, soccer, golf, or mma. "
            "A league alias such as nfl, nba, or mlb also works."
        )
    ),
]

League = Annotated[
    str,
    Field(
        description=(
            "ESPN league slug or alias, e.g. nfl, college-football, nba, wnba, "
            "mens-college-basketball, mlb, nhl, eng.1 (Premier League), usa.1 (MLS), pga, "
            "or ufc."
        )
    ),
]

# ESPN's season convention, checked live on standings, team schedules, athlete game logs
# and splits, futures, and power index.
SEASON_CONVENTION = (
    "Season by the year it ends, e.g. 2016 for the 2015-16 NBA or NHL season; for "
    "single-year sports (NFL, MLB) the year it's played."
)

Season = Annotated[
    int | None,
    Field(description=f"{SEASON_CONVENTION} Omit for the current season."),
]

AthleteSeason = Annotated[
    int | None,
    Field(description=f"{SEASON_CONVENTION} Omit for the latest season ESPN has for the athlete."),
]

MarketSeason = Annotated[
    int | None,
    Field(
        description=(
            f"{SEASON_CONVENTION} Defaults to the current calendar year, so in the fall NBA "
            "and NHL get the season that just ended; pass the next year for the season under "
            "way. Older seasons may return no items."
        )
    ),
]

AthleteStatsSeason = Annotated[
    int | None,
    Field(
        description=(
            "Echoed back as season. ESPN returns every season's totals whatever this is set "
            "to, one row per season, labelled by the year it ends for NBA and NHL (e.g. "
            '"2015-16") or the year played for NFL and MLB (e.g. "2022").'
        )
    ),
]

DraftSeason = Annotated[
    int | None,
    Field(
        description=(
            "Draft year: the year the draft is held, e.g. 2018 for the 2018 NFL Draft or 2016 "
            "for the 2016 NBA Draft (held after the 2015-16 season). Omit for ESPN's current "
            "draft year, which can be an upcoming draft."
        )
    ),
]
