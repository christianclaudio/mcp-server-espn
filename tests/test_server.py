"""Tests for FastMCP ESPN server tools, resources, prompts, transports, and caching hints."""

from unittest.mock import patch

import httpx
import pytest

from espn_mcp import server


@pytest.mark.asyncio
async def test_server_tools(mock_transport, monkeypatch):
    async_client = httpx.AsyncClient(
        transport=mock_transport, base_url="https://site.web.api.espn.com"
    )
    monkeypatch.setattr(server, "client", server.ESPNClient(http_client=async_client))

    # 1. Scoreboard
    sb = await server.get_scoreboard(sport="baseball", league="mlb", date="20260904")
    assert sb["status"] == "success"
    assert sb["data"]["count"] == 1
    assert sb["data"]["events"][0]["event_id"] == "401816789"

    # 2. Game Summary
    summary = await server.get_game_summary(sport="baseball", league="mlb", event_id="401816789")
    assert summary["status"] == "success"
    assert len(summary["data"]["betting_lines"]) == 1
    assert summary["data"]["betting_lines"][0]["provider"] == "DraftKings"

    # 3. Player Stats
    pstats = await server.get_player_stats(sport="baseball", league="mlb", event_id="401816789")
    assert pstats["status"] == "success"
    assert len(pstats["data"]["player_boxscores"]) == 1

    # 4. Standings
    standings = await server.get_standings(sport="baseball", league="mlb", season=2026)
    assert standings["status"] == "success"
    assert standings["data"]["count"] == 1

    # 5. News
    news = await server.get_news(sport="baseball", league="mlb", limit=5)
    assert news["status"] == "success"
    assert news["data"]["count"] == 1

    # 6. Rankings
    rankings = await server.get_rankings(sport="football", league="college-football")
    assert rankings["status"] == "success"
    assert len(rankings["data"]["polls"]) == 1

    # 7. Team Roster
    roster = await server.get_team_roster(sport="baseball", league="mlb", team_id="23")
    assert roster["status"] == "success"
    assert roster["data"]["count"] == 1

    # 8. Team Depth Chart
    depth = await server.get_team_depth_chart(sport="football", league="nfl", team_id="26")
    assert depth["status"] == "success"
    assert len(depth["data"]["positions"]) == 1

    # 9. Team Schedule
    schedule = await server.get_team_schedule(
        sport="baseball", league="mlb", team_id="23", season=2026
    )
    assert schedule["status"] == "success"
    assert schedule["data"]["count"] == 1

    # 10. Athlete Overview
    athlete = await server.get_athlete_overview(sport="baseball", league="mlb", athlete_id="5000")
    assert athlete["status"] == "success"
    assert "statistics" in athlete["data"]


@pytest.mark.asyncio
async def test_server_error_handling(monkeypatch):
    def error_transport(request: httpx.Request) -> httpx.Response:
        raise httpx.RequestError("Bearer secret-token-abc failure")

    async_client = httpx.AsyncClient(
        transport=httpx.MockTransport(error_transport), base_url="https://site.web.api.espn.com"
    )
    monkeypatch.setattr(
        server, "client", server.ESPNClient(http_client=async_client, max_retries=0)
    )

    sb = await server.get_scoreboard(sport="baseball", league="mlb")
    assert sb["status"] == "error"
    assert "Bearer [REDACTED]" in sb["message"]

    summary = await server.get_game_summary(sport="baseball", league="mlb", event_id="1")
    assert summary["status"] == "error"

    pstats = await server.get_player_stats(sport="baseball", league="mlb", event_id="1")
    assert pstats["status"] == "error"

    standings = await server.get_standings(sport="baseball", league="mlb")
    assert standings["status"] == "error"

    news = await server.get_news(sport="baseball", league="mlb")
    assert news["status"] == "error"

    rankings = await server.get_rankings(sport="football", league="college-football")
    assert rankings["status"] == "error"

    roster = await server.get_team_roster(sport="baseball", league="mlb", team_id="1")
    assert roster["status"] == "error"

    depth = await server.get_team_depth_chart(sport="football", league="nfl", team_id="1")
    assert depth["status"] == "error"

    schedule = await server.get_team_schedule(sport="baseball", league="mlb", team_id="1")
    assert schedule["status"] == "error"

    athlete = await server.get_athlete_overview(sport="baseball", league="mlb", athlete_id="1")
    assert athlete["status"] == "error"


def test_server_resources_and_prompts():
    leagues = server.get_supported_leagues()
    assert "mlb" in leagues
    assert "nfl" in leagues

    cap = server.get_capabilities()
    assert "ESPN MCP Server Capabilities" in cap

    p1 = server.game_analysis_prompt(sport="baseball", league="mlb", event_id="401816789")
    assert "get_game_summary" in p1
    assert "401816789" in p1

    p2 = server.team_evaluation_prompt(sport="football", league="nfl", team_id="26")
    assert "get_team_roster" in p2
    assert "26" in p2


def test_cache_hints():
    assert "tools/list" in server.CACHE_HINTS
    assert server.CACHE_HINTS["tools/list"].ttl_ms == 3600000
    assert server.CACHE_HINTS["tools/list"].scope == "public"


def test_server_main_transports(monkeypatch):
    run_args = {}

    def fake_run(**kwargs):
        nonlocal run_args
        run_args = kwargs

    monkeypatch.setattr(server.mcp, "run", fake_run)

    # stdio
    monkeypatch.setattr("sys.argv", ["espn-mcp", "--transport", "stdio"])
    server.main()
    assert run_args.get("transport") == "stdio"

    # streamable-http
    monkeypatch.setattr(
        "sys.argv",
        ["espn-mcp", "--transport", "streamable-http", "--port", "9000"],
    )
    server.main()
    assert run_args.get("transport") == "streamable-http"
    assert run_args.get("port") == 9000

    # sse
    monkeypatch.setattr("sys.argv", ["espn-mcp", "--transport", "sse", "--port", "9001"])
    server.main()
    assert run_args.get("transport") == "sse"
    assert run_args.get("port") == 9001


def test_handle_shutdown():
    with patch("os._exit") as mock_exit:
        server._handle_shutdown(15, None)
        mock_exit.assert_called_once_with(0)
