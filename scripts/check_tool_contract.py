#!/usr/bin/env python3
"""Validate tool registration counts and MCP 2.0 behavioral annotations."""

import asyncio
import sys

from espn_mcp.server import mcp

EXPECTED_TOOLS = {
    # Games domain (30 tools)
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
    "games_get_league_injuries": {"read_only": True, "destructive": False},
    "games_get_drives": {"read_only": True, "destructive": False},
    "games_get_drive_plays": {"read_only": True, "destructive": False},
    "games_get_qbr": {"read_only": True, "destructive": False},
    "games_list_golf_tournaments": {"read_only": True, "destructive": False},
    "games_get_golf_tournament": {"read_only": True, "destructive": False},
    "games_get_bets": {"read_only": True, "destructive": False},
    "games_get_bet_type": {"read_only": True, "destructive": False},
    "games_get_awards": {"read_only": True, "destructive": False},
    "games_get_award": {"read_only": True, "destructive": False},
    # Teams domain (21 tools)
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
    "teams_list_coaches": {"read_only": True, "destructive": False},
    "teams_get_coach": {"read_only": True, "destructive": False},
    "teams_list_venues": {"read_only": True, "destructive": False},
    "teams_get_venue": {"read_only": True, "destructive": False},
    "teams_list_franchises": {"read_only": True, "destructive": False},
    "teams_get_franchise": {"read_only": True, "destructive": False},
    "teams_get_team_tournament_history": {"read_only": True, "destructive": False},
    "teams_list_recruits": {"read_only": True, "destructive": False},
    "teams_get_recruiting_rankings": {"read_only": True, "destructive": False},
    # News domain (1 tool)
    "news_get_news": {"read_only": True, "destructive": False},
}


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

    print("[✓] All tool contracts and MCP 2.0 annotations verified successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(verify_contracts()))
