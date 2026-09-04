#!/usr/bin/env python3
"""Execute 1-by-1 live smoke tests against real ESPN public endpoints.

Exercises all 10 tools sequentially against live network endpoints,
validates response schemas, measures latency, and prints a summary.
"""

import asyncio
import sys
import time

from espn_mcp import server


async def run_live_tests() -> int:
    print("=" * 75)
    print("🏈 ESPN MCP SERVER — 1-BY-1 LIVE ENDPOINT SMOKE TEST")
    print("=" * 75)

    results: list[tuple[str, str, float, str]] = []
    real_event_id: str | None = None
    real_athlete_id: str | None = None

    # 1. Scoreboard (MLB)
    t0 = time.perf_counter()
    try:
        res = await server.get_scoreboard(sport="baseball", league="mlb")
        dt = (time.perf_counter() - t0) * 1000
        if res.get("status") == "success":
            events = res["data"].get("events", [])
            count = len(events)
            if count > 0:
                real_event_id = events[0].get("event_id")
            detail = f"{count} games found (e.g. event={real_event_id})"
            results.append(("get_scoreboard", "PASS", dt, detail))
        else:
            results.append(("get_scoreboard", "FAIL", dt, res.get("message", "Error")))
    except Exception as exc:
        results.append(("get_scoreboard", "ERROR", (time.perf_counter() - t0) * 1000, str(exc)))

    if not real_event_id:
        real_event_id = "401569483"

    # 2. Game Summary
    t0 = time.perf_counter()
    try:
        res = await server.get_game_summary(sport="baseball", league="mlb", event_id=real_event_id)
        dt = (time.perf_counter() - t0) * 1000
        if res.get("status") == "success":
            data = res.get("data", {})
            lines = len(data.get("betting_lines", []))
            has_pred = "predictor" in data
            detail = f"{lines} betting lines, predictor={'yes' if has_pred else 'no'}"
            results.append(("get_game_summary", "PASS", dt, detail))
        else:
            results.append(("get_game_summary", "FAIL", dt, res.get("message", "Error")))
    except Exception as exc:
        results.append(("get_game_summary", "ERROR", (time.perf_counter() - t0) * 1000, str(exc)))

    # 3. Player Stats
    t0 = time.perf_counter()
    try:
        res = await server.get_player_stats(sport="baseball", league="mlb", event_id=real_event_id)
        dt = (time.perf_counter() - t0) * 1000
        if res.get("status") == "success":
            data = res.get("data", {})
            box = len(data.get("player_boxscores", []))
            detail = f"{box} team player boxscore categories"
            results.append(("get_player_stats", "PASS", dt, detail))
        else:
            results.append(("get_player_stats", "FAIL", dt, res.get("message", "Error")))
    except Exception as exc:
        results.append(("get_player_stats", "ERROR", (time.perf_counter() - t0) * 1000, str(exc)))

    # 4. Standings (MLB)
    t0 = time.perf_counter()
    try:
        res = await server.get_standings(sport="baseball", league="mlb")
        dt = (time.perf_counter() - t0) * 1000
        if res.get("status") == "success":
            count = res["data"].get("count", 0)
            detail = f"{count} divisions/groups parsed"
            results.append(("get_standings", "PASS", dt, detail))
        else:
            results.append(("get_standings", "FAIL", dt, res.get("message", "Error")))
    except Exception as exc:
        results.append(("get_standings", "ERROR", (time.perf_counter() - t0) * 1000, str(exc)))

    # 5. News (MLB)
    t0 = time.perf_counter()
    try:
        res = await server.get_news(sport="baseball", league="mlb", limit=3)
        dt = (time.perf_counter() - t0) * 1000
        if res.get("status") == "success":
            count = res["data"].get("count", 0)
            detail = f"{count} headlines retrieved"
            results.append(("get_news", "PASS", dt, detail))
        else:
            results.append(("get_news", "FAIL", dt, res.get("message", "Error")))
    except Exception as exc:
        results.append(("get_news", "ERROR", (time.perf_counter() - t0) * 1000, str(exc)))

    # 6. Rankings (College Football)
    t0 = time.perf_counter()
    try:
        res = await server.get_rankings(sport="football", league="college-football")
        dt = (time.perf_counter() - t0) * 1000
        if res.get("status") == "success":
            polls = len(res["data"].get("polls", []))
            detail = f"{polls} Top 25 polls retrieved"
            results.append(("get_rankings", "PASS", dt, detail))
        else:
            results.append(("get_rankings", "FAIL", dt, res.get("message", "Error")))
    except Exception as exc:
        results.append(("get_rankings", "ERROR", (time.perf_counter() - t0) * 1000, str(exc)))

    # 7. Team Roster (MLB Pittsburgh Pirates id=23)
    t0 = time.perf_counter()
    try:
        res = await server.get_team_roster(sport="baseball", league="mlb", team_id="23")
        dt = (time.perf_counter() - t0) * 1000
        if res.get("status") == "success":
            athletes = res["data"].get("athletes", [])
            count = len(athletes)
            if count > 0:
                real_athlete_id = athletes[0].get("id")
            detail = f"{count} active players in roster (e.g. athlete={real_athlete_id})"
            results.append(("get_team_roster", "PASS", dt, detail))
        else:
            results.append(("get_team_roster", "FAIL", dt, res.get("message", "Error")))
    except Exception as exc:
        results.append(("get_team_roster", "ERROR", (time.perf_counter() - t0) * 1000, str(exc)))

    # 8. Team Depth Chart (NFL San Francisco 49ers id=25)
    t0 = time.perf_counter()
    try:
        res = await server.get_team_depth_chart(sport="football", league="nfl", team_id="25")
        dt = (time.perf_counter() - t0) * 1000
        if res.get("status") == "success":
            pos = len(res["data"].get("positions", []))
            detail = f"{pos} positional depth tiers"
            results.append(("get_team_depth_chart", "PASS", dt, detail))
        else:
            results.append(("get_team_depth_chart", "FAIL", dt, res.get("message", "Error")))
    except Exception as exc:
        err_dt = (time.perf_counter() - t0) * 1000
        results.append(("get_team_depth_chart", "ERROR", err_dt, str(exc)))

    # 9. Team Schedule (MLB Pittsburgh Pirates id=23)
    t0 = time.perf_counter()
    try:
        res = await server.get_team_schedule(sport="baseball", league="mlb", team_id="23")
        dt = (time.perf_counter() - t0) * 1000
        if res.get("status") == "success":
            count = res["data"].get("count", 0)
            detail = f"{count} season games mapped"
            results.append(("get_team_schedule", "PASS", dt, detail))
        else:
            results.append(("get_team_schedule", "FAIL", dt, res.get("message", "Error")))
    except Exception as exc:
        err_dt = (time.perf_counter() - t0) * 1000
        results.append(("get_team_schedule", "ERROR", err_dt, str(exc)))

    # 10. Athlete Overview (Using real athlete ID from roster step, e.g. 41282)
    if not real_athlete_id:
        real_athlete_id = "41282"

    t0 = time.perf_counter()
    try:
        res = await server.get_athlete_overview(
            sport="baseball", league="mlb", athlete_id=real_athlete_id
        )
        dt = (time.perf_counter() - t0) * 1000
        if res.get("status") == "success":
            name = res["data"].get("athlete", {}).get("name", "Unknown")
            detail = f"Athlete: {name} (id={real_athlete_id})"
            results.append(("get_athlete_overview", "PASS", dt, detail))
        else:
            results.append(("get_athlete_overview", "FAIL", dt, res.get("message", "Error")))
    except Exception as exc:
        err_dt = (time.perf_counter() - t0) * 1000
        results.append(("get_athlete_overview", "ERROR", err_dt, str(exc)))

    await server.client.close()

    print(f"\n{'Tool':<25} | {'Status':<6} | {'Latency':<9} | {'Details'}")
    print("-" * 75)
    all_passed = True
    for tool_name, status, lat, detail in results:
        icon = "✓" if status == "PASS" else "✗"
        print(f"{tool_name:<25} | {icon} {status:<4} | {lat:6.1f}ms | {detail}")
        if status != "PASS":
            all_passed = False

    print("=" * 75)
    if all_passed:
        print("[✓] ALL 10 TOOLS PASSED 1-BY-1 LIVE NETWORK VERIFICATION!")
        return 0
    else:
        print("[x] ONE OR MORE TOOLS FAILED LIVE NETWORK VERIFICATION!")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(run_live_tests()))
