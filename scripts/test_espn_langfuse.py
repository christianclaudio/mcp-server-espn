"""Automated MCP tool readiness tester with Langfuse observability."""

import asyncio
import time
from typing import Any

from langfuse import Langfuse, observe
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

lf = Langfuse()


@observe(as_type="tool")
async def invoke_mcp_tool(
    session: ClientSession, tool_name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Execute a single MCP tool and capture observability data in Langfuse."""
    start = time.perf_counter()
    res = await session.call_tool(tool_name, arguments)
    elapsed_ms = round((time.perf_counter() - start) * 1000, 2)

    is_error = getattr(res, "isError", False)
    content_text = ""
    if hasattr(res, "content") and res.content:
        for item in res.content:
            text = getattr(item, "text", str(item))
            content_text += text

    return {
        "tool": tool_name,
        "arguments": arguments,
        "is_error": is_error,
        "latency_ms": elapsed_ms,
        "output_chars": len(content_text),
        "preview": content_text[:200] + "..." if len(content_text) > 200 else content_text,
    }


@observe(name="mcp-fleet-readiness-espn")
async def run_readiness_audit() -> list[dict[str, Any]]:
    """Execute all registered ESPN tools and emit a unified Langfuse trace."""
    params = StdioServerParameters(
        command="uv",
        args=["run", "--directory", "/home/chris31jct/vscode/mcp-server-espn", "espn-mcp"],
    )

    results: list[dict[str, Any]] = []

    print("\n========================================================")
    print("🚀 ESPN MCP Fleet Readiness & Langfuse Observability Run")
    print("========================================================\n")

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools_list = await session.list_tools()
            available_tools = {t.name: t for t in tools_list.tools}
            print(f"[*] Discovered {len(available_tools)} registered MCP tools.\n")

            # 1. get_scoreboard
            print("[1/10] Testing get_scoreboard (MLB)...")
            results.append(
                await invoke_mcp_tool(
                    session, "get_scoreboard", {"sport": "baseball", "league": "mlb"}
                )
            )

            # 2. get_news
            print("[2/10] Testing get_news (NBA)...")
            results.append(
                await invoke_mcp_tool(
                    session, "get_news", {"sport": "basketball", "league": "nba", "limit": 2}
                )
            )

            # 3. get_standings
            print("[3/10] Testing get_standings (NFL)...")
            results.append(
                await invoke_mcp_tool(
                    session, "get_standings", {"sport": "football", "league": "nfl"}
                )
            )

            # 4. get_rankings
            print("[4/10] Testing get_rankings (College Football)...")
            results.append(
                await invoke_mcp_tool(
                    session, "get_rankings", {"sport": "football", "league": "college-football"}
                )
            )

            # 5. get_team_roster
            print("[5/10] Testing get_team_roster (Yankees ID 10)...")
            results.append(
                await invoke_mcp_tool(
                    session,
                    "get_team_roster",
                    {"sport": "baseball", "league": "mlb", "team_id": "10"},
                )
            )

            # 6. get_team_depth_chart
            print("[6/10] Testing get_team_depth_chart (Chiefs ID 12)...")
            results.append(
                await invoke_mcp_tool(
                    session,
                    "get_team_depth_chart",
                    {"sport": "football", "league": "nfl", "team_id": "12"},
                )
            )

            # 7. get_team_schedule
            print("[7/10] Testing get_team_schedule (Lakers ID 13)...")
            results.append(
                await invoke_mcp_tool(
                    session,
                    "get_team_schedule",
                    {"sport": "basketball", "league": "nba", "team_id": "13"},
                )
            )

            # 8. get_athlete_overview
            print("[8/10] Testing get_athlete_overview (LeBron James 1966)...")
            results.append(
                await invoke_mcp_tool(
                    session,
                    "get_athlete_overview",
                    {"sport": "basketball", "league": "nba", "athlete_id": "1966"},
                )
            )

            # 9. get_game_summary
            print("[9/10] Testing get_game_summary (MLB sample event)...")
            results.append(
                await invoke_mcp_tool(
                    session,
                    "get_game_summary",
                    {"sport": "baseball", "league": "mlb", "event_id": "401695420"},
                )
            )

            # 10. get_player_stats
            print("[10/10] Testing get_player_stats (MLB sample event)...")
            results.append(
                await invoke_mcp_tool(
                    session,
                    "get_player_stats",
                    {"sport": "baseball", "league": "mlb", "event_id": "401695420"},
                )
            )

    return results


def main() -> None:
    results = asyncio.run(run_readiness_audit())
    lf.flush()

    print("\n========================================================")
    print("📊 ESPN READINESS SCORECARD")
    print("========================================================\n")
    print(
        f"| {'#':<2} | {'Tool Name':<22} | {'Status':<6} | {'Latency':<9} | {'Output Size':<12} |"
    )
    print(f"|{'-' * 4}|{'-' * 24}|{'-' * 8}|{'-' * 11}|{'-' * 14}|")
    for i, r in enumerate(results, 1):
        status = "PASS" if not r["is_error"] else "FAIL"
        lat = f"{r['latency_ms']}ms"
        sz = f"{r['output_chars']} chars"
        print(f"| {i:<2} | {r['tool']:<22} | {status:<6} | {lat:<9} | {sz:<12} |")

    print("\n--------------------------------------------------------")
    print("🔗 View live trace waterfall in Langfuse Cloud:")
    print("   https://us.cloud.langfuse.com")
    print("--------------------------------------------------------\n")


if __name__ == "__main__":
    main()
