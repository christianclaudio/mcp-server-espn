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

Season = Annotated[
    int | None,
    Field(
        description=(
            "Season by the year it ends, e.g. 2016 for the 2015-16 NBA or NHL season; for "
            "single-year sports (NFL, MLB) the year it's played. Omit for the current season."
        )
    ),
]
