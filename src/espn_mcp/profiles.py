"""Server profiles and annotation-driven tool visibility.

A profile is one of two kinds; both coexist and both are selected with
``--profile`` / ``ESPN_MCP_PROFILE``:

* **Domain-mount profile** — ``domains`` names the domain sub-servers to mount.
  Use it when a job maps cleanly onto whole domains (``full``, ``games``, ``teams``, ``news``).
* **Allowlist profile** — ``tools`` is an explicit set of client-visible tool names
  applied over the full mounted catalog. Use it for job-shaped profiles that cut
  across domains (``gameday``, ``betting``, ``scouting``, ``season``).
  Filtering is tools-only: prompts and resources stay.

``readonly=True`` keeps only tools whose MCP ``readOnlyHint`` annotation is ``True``.
The annotation is the single source of truth for read-only classification; a missing
annotation or ``readOnlyHint`` other than ``True`` counts as a write (fail closed).
Every ESPN tool is a read of the public ESPN APIs and is annotated ``readOnlyHint=True``,
so ``readonly`` lists the whole catalog; the gate still refuses any tool that loses the
annotation.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass

from fastmcp.server.transforms import GetToolNext, Transform
from fastmcp.tools import Tool
from fastmcp.utilities.versions import VersionSpec
from mcp.types import ToolAnnotations

ALL_DOMAINS: tuple[str, ...] = ("games", "teams", "news")

# Job (allowlist) profile tool lists, in catalog order.
GAMEDAY_TOOLS: frozenset[str] = frozenset(
    {
        "games_get_scoreboard",
        "games_get_game_summary",
        "games_get_league_events",
        "games_get_scoreboard_header",
        "games_get_play_by_play",
        "games_get_game_situation",
        "games_get_win_probabilities",
        "games_get_calendar",
        "teams_get_player_stats",
        "teams_search",
        "news_get_news",
    }
)

BETTING_TOOLS: frozenset[str] = frozenset(
    {
        "games_get_scoreboard",
        "games_get_game_summary",
        "games_get_team_schedule",
        "games_get_standings",
        "games_get_event_odds",
        "games_get_win_probabilities",
        "games_get_game_predictor",
        "games_get_futures",
        "games_get_power_index",
        "teams_get_team_roster",
        "teams_search",
        "teams_get_team_statistics",
        "news_get_news",
    }
)

SCOUTING_TOOLS: frozenset[str] = frozenset(
    {
        "games_get_transactions",
        "games_get_leaders_by_athlete",
        "games_get_league_draft",
        "teams_get_team_roster",
        "teams_get_team_depth_chart",
        "teams_get_player_stats",
        "teams_get_athlete_overview",
        "teams_search",
        "teams_list_teams",
        "teams_get_athlete_bio",
        "teams_get_athlete_stats",
        "teams_get_athlete_gamelog",
        "teams_get_athlete_splits",
        "news_get_news",
    }
)

SEASON_TOOLS: frozenset[str] = frozenset(
    {
        "games_get_team_schedule",
        "games_get_standings",
        "games_get_rankings",
        "games_get_leaders_by_athlete",
        "games_get_leaders_by_team",
        "games_get_league_groups",
        "games_get_league_events",
        "games_get_league_draft",
        "games_get_calendar",
        "games_get_power_index",
        "teams_search",
        "teams_list_teams",
        "teams_get_team",
        "teams_get_team_statistics",
    }
)


@dataclass(frozen=True)
class Profile:
    """A named server profile: domain mounts or a tool-name allowlist."""

    name: str
    job: str
    domains: tuple[str, ...] = ALL_DOMAINS
    tools: frozenset[str] | None = None
    readonly: bool = False

    @property
    def is_allowlist(self) -> bool:
        """True when the profile is an explicit tool-name allowlist."""
        return self.tools is not None


PROFILES: dict[str, Profile] = {
    profile.name: profile
    for profile in (
        # Domain-mount profiles
        Profile(
            name="full",
            job="Complete catalog: every tool in the games, teams and news domains.",
        ),
        Profile(
            name="games",
            job="Scores, schedules, standings, odds, play-by-play and league data (games domain).",
            domains=("games",),
        ),
        Profile(
            name="teams",
            job=(
                "Teams, rosters, depth charts, athlete profiles and stats, and entity search "
                "(teams domain)."
            ),
            domains=("teams",),
        ),
        Profile(
            name="news",
            job="League news headlines, injury updates and roster moves (news domain).",
            domains=("news",),
        ),
        Profile(
            name="readonly",
            job="Inspect without side effects: every tool annotated readOnlyHint=True.",
            readonly=True,
        ),
        # Allowlist (job-shaped) profiles: span domains, names are client-visible names
        Profile(
            name="gameday",
            job=(
                "Fan or broadcaster follows today's games live: scoreboards, game situation, "
                "play-by-play, win probability, box scores and breaking news."
            ),
            tools=GAMEDAY_TOOLS,
        ),
        Profile(
            name="betting",
            job=(
                "Bettor or prediction-market resolver compares odds, futures, matchup predictions "
                "and power ratings, checks injuries, and confirms final results."
            ),
            tools=BETTING_TOOLS,
        ),
        Profile(
            name="scouting",
            job=(
                "Fantasy manager or scout evaluates athletes and rosters: depth charts, injuries, "
                "bios, stats, splits, game logs, leaderboards, transactions and the draft."
            ),
            tools=SCOUTING_TOOLS,
        ),
        Profile(
            name="season",
            job=(
                "League analyst researches a season: standings, rankings, power index, team "
                "statistics and leaders, schedules, league structure and the draft."
            ),
            tools=SEASON_TOOLS,
        ),
    )
}

# Tools in no job (allowlist) profile. They stay reachable in ``full`` and in their
# domain-mount profile. Tests require every tool in ``full`` to be in a job profile or
# listed here, so a new tool is placed on purpose. Every ESPN tool is in a job profile.
FULL_ONLY_TOOLS: frozenset[str] = frozenset()

READ_ONLY_ANNOTATIONS = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)


def get_profile(name: str) -> Profile:
    """Return the named profile or raise ``ValueError`` listing valid profiles."""
    key = name.lower()
    if key not in PROFILES:
        valid = ", ".join(sorted(PROFILES))
        raise ValueError(f"Unknown profile {name!r}; valid profiles: {valid}.")
    return PROFILES[key]


def validate_allowlist(profile: Profile, catalog: Collection[str]) -> frozenset[str]:
    """Return the profile allowlist, raising ``ValueError`` on any name not in ``catalog``."""
    allowlist = profile.tools or frozenset()
    unknown = sorted(allowlist - set(catalog))
    if unknown:
        raise ValueError(
            f"Profile {profile.name!r} allowlists tools not in the full catalog: "
            f"{', '.join(unknown)}."
        )
    return allowlist


def is_read_only_tool(tool: Tool | None) -> bool:
    """Return True only for a tool annotated ``readOnlyHint=True`` (fail closed)."""
    if tool is None or tool.annotations is None:
        return False
    return tool.annotations.read_only_hint is True


class ReadOnlyToolFilter(Transform):
    """Keep only tools annotated ``readOnlyHint=True``; other component types pass through."""

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [tool for tool in tools if is_read_only_tool(tool)]

    async def get_tool(
        self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None
    ) -> Tool | None:
        tool = await call_next(name, version=version)
        return tool if is_read_only_tool(tool) else None


class ReadOnlyAnnotations(Transform):
    """Annotate named synthetic discovery tools as ``readOnlyHint=True``.

    FastMCP's synthetic discovery tools (``search_tools``; Code Mode ``search`` /
    ``get_schema``) ship without annotations. They only read the catalog, so the
    server marks them read-only to keep discovery usable under the readonly gate.
    ``call_tool`` / ``execute`` are deliberately not annotated: the gate classifies
    ``call_tool`` by the tool it proxies, and ``execute`` stays refused under readonly.
    """

    def __init__(self, names: Collection[str]) -> None:
        self.names = frozenset(names)

    def _annotate(self, tool: Tool) -> Tool:
        if tool.name not in self.names:
            return tool
        return tool.model_copy(update={"annotations": READ_ONLY_ANNOTATIONS})

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [self._annotate(tool) for tool in tools]

    async def get_tool(
        self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None
    ) -> Tool | None:
        tool = await call_next(name, version=version)
        return None if tool is None else self._annotate(tool)
