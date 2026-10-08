#!/usr/bin/env python3
"""Validate tool registration counts, MCP 2.0 behavioral annotations, and profile counts."""

import asyncio
import sys

from espn_mcp.profiles import FULL_ONLY_TOOLS, PROFILES, is_read_only_tool
from espn_mcp.server import create_server, mcp

EXPECTED_TOOLS = {
    # Games domain (20 tools)
    "games_get_scoreboard": {"read_only": True, "destructive": False},
    "games_get_game_summary": {"read_only": True, "destructive": False},
    "games_get_team_schedule": {"read_only": True, "destructive": False},
    "games_get_standings": {"read_only": True, "destructive": False},
    "games_get_rankings": {"read_only": True, "destructive": False},
    "games_get_transactions": {"read_only": True, "destructive": False},
    "games_get_leaders_by_athlete": {"read_only": True, "destructive": False},
    "games_get_leaders_by_team": {"read_only": True, "destructive": False},
    "games_get_league_groups": {"read_only": True, "destructive": False},
    "games_get_league_events": {"read_only": True, "destructive": False},
    "games_get_league_draft": {"read_only": True, "destructive": False},
    "games_get_scoreboard_header": {"read_only": True, "destructive": False},
    "games_get_event_odds": {"read_only": True, "destructive": False},
    "games_get_play_by_play": {"read_only": True, "destructive": False},
    "games_get_game_situation": {"read_only": True, "destructive": False},
    "games_get_win_probabilities": {"read_only": True, "destructive": False},
    "games_get_game_predictor": {"read_only": True, "destructive": False},
    "games_get_calendar": {"read_only": True, "destructive": False},
    "games_get_futures": {"read_only": True, "destructive": False},
    "games_get_power_index": {"read_only": True, "destructive": False},
    # Teams domain (12 tools)
    "teams_search": {"read_only": True, "destructive": False},
    "teams_list_teams": {"read_only": True, "destructive": False},
    "teams_get_team": {"read_only": True, "destructive": False},
    "teams_get_team_statistics": {"read_only": True, "destructive": False},
    "teams_get_team_roster": {"read_only": True, "destructive": False},
    "teams_get_team_depth_chart": {"read_only": True, "destructive": False},
    "teams_get_player_stats": {"read_only": True, "destructive": False},
    "teams_get_athlete_overview": {"read_only": True, "destructive": False},
    "teams_get_athlete_bio": {"read_only": True, "destructive": False},
    "teams_get_athlete_stats": {"read_only": True, "destructive": False},
    "teams_get_athlete_gamelog": {"read_only": True, "destructive": False},
    "teams_get_athlete_splits": {"read_only": True, "destructive": False},
    # News domain (1 tool)
    "news_get_news": {"read_only": True, "destructive": False},
}

# Expected (listed tools, tools annotated readOnlyHint=True) per profile, flat tools/list.
EXPECTED_PROFILE_COUNTS: dict[str, tuple[int, int]] = {
    # Domain-mount profiles
    "full": (33, 33),
    "games": (20, 20),
    "teams": (12, 12),
    "news": (1, 1),
    "readonly": (33, 33),
    # Job (allowlist) profiles
    "gameday": (11, 11),
    "betting": (13, 13),
    "scouting": (14, 14),
    "season": (14, 14),
}

# Tools in no job profile (reachable in full and their domain-mount profile only).
EXPECTED_FULL_ONLY: frozenset[str] = frozenset()


async def verify_profiles() -> int:
    """Check every profile's listed and read-only counts and the full-only placement."""
    if set(PROFILES) != set(EXPECTED_PROFILE_COUNTS):
        print(
            f"[x] Error: Profile set mismatch: got {sorted(PROFILES)}, "
            f"expected {sorted(EXPECTED_PROFILE_COUNTS)}"
        )
        return 1
    for name, expected in EXPECTED_PROFILE_COUNTS.items():
        tools = await create_server(profile=name).list_tools()
        actual = (len(tools), sum(1 for t in tools if is_read_only_tool(t)))
        if actual != expected:
            print(f"[x] Error: Profile '{name}' (tools, read-only) {actual} != {expected}")
            return 1
        print(f"[✓] Profile '{name}': {actual[0]} tools, {actual[1]} read-only")
    if FULL_ONLY_TOOLS != EXPECTED_FULL_ONLY:
        print(f"[x] Error: FULL_ONLY_TOOLS {sorted(FULL_ONLY_TOOLS)} != expected")
        return 1
    return 0


async def verify_contracts() -> int:
    tools = await mcp.list_tools()
    tool_map = {t.name: t for t in tools}

    print(f"[*] Validating {len(tools)} registered MCP tools...")

    if len(tool_map) != len(EXPECTED_TOOLS):
        print(
            f"[x] Error: Registered tool count mismatch: "
            f"got {len(tool_map)}, expected {len(EXPECTED_TOOLS)}"
        )
        return 1

    for name, expected in EXPECTED_TOOLS.items():
        if name not in tool_map:
            print(f"[x] Error: Missing expected tool '{name}'")
            return 1
        t = tool_map[name]
        ann = t.annotations
        if ann is None:
            print(f"[x] Error: Tool '{name}' has no MCP 2.0 annotations!")
            return 1
        read_only = (
            ann.read_only_hint
            if hasattr(ann, "read_only_hint")
            else getattr(ann, "readOnlyHint", None)
        )
        destructive = (
            ann.destructive_hint
            if hasattr(ann, "destructive_hint")
            else getattr(ann, "destructiveHint", None)
        )
        if read_only != expected["read_only"]:
            print(
                f"[x] Error: Tool '{name}' read_only_hint mismatch: "
                f"{read_only} != {expected['read_only']}"
            )
            return 1
        if destructive != expected["destructive"]:
            print(
                f"[x] Error: Tool '{name}' destructive_hint mismatch: "
                f"{destructive} != {expected['destructive']}"
            )
            return 1

    if await verify_profiles():
        return 1

    print("[✓] All tool contracts, MCP 2.0 annotations, and profiles verified successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(verify_contracts()))
