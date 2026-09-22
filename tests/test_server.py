"""Tests for FastMCP ESPN server tools, resources, prompts, transports, and caching hints."""

from unittest.mock import MagicMock

import httpx
import pytest

from espn_mcp import server


@pytest.mark.asyncio
async def test_server_tools(mock_transport, monkeypatch):
    """Verify all 10 domain tools execute successfully through FastMCP handlers."""
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

    # 11. Search
    srch = await server.search(query="Stafford", type="player", limit=5)
    assert srch["status"] == "success"
    assert srch["data"]["count"] == 1
    assert srch["data"]["items"][0]["name"] == "Matthew Stafford"

    # 12. List Teams
    teams = await server.list_teams(sport="football", league="nfl")
    assert teams["status"] == "success"
    assert teams["data"]["count"] == 1
    assert teams["data"]["teams"][0]["id"] == "14"

    # 13. Get Team Detail
    team_detail = await server.get_team(sport="football", league="nfl", team_id="14")
    assert team_detail["status"] == "success"
    assert team_detail["data"]["name"] == "Los Angeles Rams"
    assert team_detail["data"]["venue"] == "SoFi Stadium"

    # 14. Get Team Statistics
    team_stats = await server.get_team_statistics(sport="football", league="nfl", team_id="14")
    assert team_stats["status"] == "success"
    assert len(team_stats["data"]["team_stats"]) == 1
    assert len(team_stats["data"]["opponent_stats"]) == 1

    # 15. Get Transactions
    tx = await server.get_transactions(sport="football", league="nfl", limit=25)
    assert tx["status"] == "success"
    assert tx["data"]["count"] == 1
    assert "Matthew Stafford" in tx["data"]["transactions"][0]["description"]

    # 16. Athlete Bio
    bio = await server.get_athlete_bio(sport="football", league="nfl", athlete_id="12483")
    assert bio["status"] == "success"
    assert bio["data"]["bio"]["college"]["name"] == "Georgia"

    # 17. Athlete Stats
    stats = await server.get_athlete_stats(sport="football", league="nfl", athlete_id="12483")
    assert stats["status"] == "success"
    assert "passing" in stats["data"]["statistics"]["categories"][0]["name"]

    # 18. Athlete Gamelog
    gamelog = await server.get_athlete_gamelog(sport="football", league="nfl", athlete_id="12483")
    assert gamelog["status"] == "success"
    assert gamelog["data"]["count"] == 1

    # 19. Athlete Splits
    splits = await server.get_athlete_splits(sport="football", league="nfl", athlete_id="12483")
    assert splits["status"] == "success"
    assert "home" in splits["data"]["splits"]["categories"][0]["name"]

    # 20. Leaders by Athlete
    l_ath = await server.get_leaders_by_athlete(sport="football", league="nfl")
    assert l_ath["status"] == "success"
    assert l_ath["data"]["leaders"][0]["athlete"]["displayName"] == "Matthew Stafford"

    # 21. Leaders by Team
    l_team = await server.get_leaders_by_team(sport="football", league="nfl")
    assert l_team["status"] == "success"
    assert l_team["data"]["leaders"][0]["team"]["displayName"] == "Los Angeles Rams"

    # 22. League Groups
    groups = await server.get_league_groups(sport="football", league="nfl")
    assert groups["status"] == "success"
    assert groups["data"]["count"] == 1

    # 23. League Events
    events = await server.get_league_events(sport="football", league="nfl")
    assert events["status"] == "success"
    assert events["data"]["count"] == 1

    # 24. League Draft
    draft = await server.get_league_draft(sport="football", league="nfl")
    assert draft["status"] == "success"
    assert draft["data"]["draft"]["year"] == 2026

    # 25. Scoreboard Header
    hdr = await server.get_scoreboard_header(sport="football", league="nfl")
    assert hdr["status"] == "success"
    assert "sports" in hdr["data"]

    # 26. Event Odds
    odds = await server.get_event_odds(sport="football", league="nfl", event_id="401872947")
    assert odds["status"] == "success"
    assert odds["data"]["odds"][0]["provider"]["name"] == "DraftKings"

    # 27. Play by Play
    pbp = await server.get_play_by_play(sport="football", league="nfl", event_id="401872947")
    assert pbp["status"] == "success"
    assert pbp["data"]["count"] == 1

    # 28. Game Situation
    sit = await server.get_game_situation(sport="football", league="nfl", event_id="401872947")
    assert sit["status"] == "success"
    assert sit["data"]["situation"]["down"] == 3

    # 29. Win Probabilities
    probs = await server.get_win_probabilities(sport="football", league="nfl", event_id="401872947")
    assert probs["status"] == "success"
    assert probs["data"]["count"] == 1

    # 30. Game Predictor
    pred = await server.get_game_predictor(sport="football", league="nfl", event_id="401872947")
    assert pred["status"] == "success"
    assert pred["data"]["predictor"]["homeTeam"]["gameProjection"] == 65.4

    # 31. Calendar
    cal = await server.get_calendar(sport="football", league="nfl")
    assert cal["status"] == "success"
    assert "dates" in cal["data"]["calendar"]

    # 32. Futures
    fut = await server.get_futures(sport="football", league="nfl")
    assert fut["status"] == "success"
    assert fut["data"]["season"] == 2026

    # 33. Power Index
    fpi = await server.get_power_index(sport="football", league="nfl")
    assert fpi["status"] == "success"
    assert fpi["data"]["power_index"][0]["rank"] == 4


@pytest.mark.asyncio
async def test_server_error_handling(monkeypatch):
    """Verify secret redaction and error formatting across tool handlers."""

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

    srch = await server.search(query="test")
    assert srch["status"] == "error"

    lt = await server.list_teams(sport="football", league="nfl")
    assert lt["status"] == "error"

    gt = await server.get_team(sport="football", league="nfl", team_id="1")
    assert gt["status"] == "error"

    gts = await server.get_team_statistics(sport="football", league="nfl", team_id="1")
    assert gts["status"] == "error"

    gtx = await server.get_transactions(sport="football", league="nfl")
    assert gtx["status"] == "error"


def test_server_resources_and_prompts():
    """Verify resources and prompts return expected metadata and guidance."""
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
    """Verify RFC 9111 caching hints match configured catalog TTL defaults."""
    expected_keys = {
        "tools/list",
        "prompts/list",
        "resources/list",
        "resources/templates/list",
        "server/discover",
    }
    assert set(server.CACHE_HINTS.keys()) == expected_keys
    for _, hint in server.CACHE_HINTS.items():
        assert hint.ttl_ms == server.settings.CATALOG_CACHE_TTL_MS
        assert hint.scope == "public"


def test_server_main_transports(monkeypatch, caplog, mock_transport):
    """Verify CLI transport selection, flag parsing, and deprecation warnings."""
    monkeypatch.setattr(server.signal, "signal", lambda *_args, **_kwargs: None)
    run_args = {}

    def fake_run(self, **kwargs):
        nonlocal run_args
        run_args = kwargs

    monkeypatch.setattr(server.FastMCP, "run", fake_run)

    # stdio default
    monkeypatch.setattr("sys.argv", ["espn-mcp", "--transport", "stdio"])
    server.main()
    assert run_args.get("transport") == "stdio"

    # stdio with stateless flags triggers warnings
    caplog.clear()
    monkeypatch.setattr(
        "sys.argv",
        ["espn-mcp", "--transport", "stdio", "--stateless", "--json-response"],
    )
    server.main()
    assert run_args.get("transport") == "stdio"
    assert any("--stateless flag is only applicable" in r.message for r in caplog.records)
    assert any("--json-response flag is only applicable" in r.message for r in caplog.records)

    # streamable-http default
    monkeypatch.setattr(server.settings, "MCP_STATELESS_HTTP", False)
    monkeypatch.setattr(server.settings, "MCP_JSON_RESPONSE", False)
    monkeypatch.setattr(
        "sys.argv",
        ["espn-mcp", "--transport", "streamable-http", "--port", "9000"],
    )
    server.main()
    assert run_args.get("transport") == "streamable-http"
    assert run_args.get("host") == "127.0.0.1"
    assert run_args.get("port") == 9000
    assert run_args.get("stateless_http") is False
    assert run_args.get("json_response") is False

    # streamable-http explicit stateless and json-response
    monkeypatch.setattr(
        "sys.argv",
        [
            "espn-mcp",
            "--transport",
            "streamable-http",
            "--port",
            "9002",
            "--stateless",
            "--json-response",
        ],
    )
    server.main()
    assert run_args.get("transport") == "streamable-http"
    assert run_args.get("port") == 9002
    assert run_args.get("stateless_http") is True
    assert run_args.get("json_response") is True

    # streamable-http explicit negation flags
    monkeypatch.setattr(
        "sys.argv",
        [
            "espn-mcp",
            "--transport",
            "streamable-http",
            "--port",
            "9003",
            "--no-stateless",
            "--no-json-response",
        ],
    )
    server.main()
    assert run_args.get("transport") == "streamable-http"
    assert run_args.get("port") == 9003
    assert run_args.get("stateless_http") is False
    assert run_args.get("json_response") is False

    # sse
    monkeypatch.setattr("sys.argv", ["espn-mcp", "--transport", "sse", "--port", "9001"])
    server.main()
    assert run_args.get("transport") == "sse"
    assert run_args.get("host") == "127.0.0.1"
    assert run_args.get("port") == 9001

    # streamable-http with allowed-host and allowed-origin
    monkeypatch.setattr(
        "sys.argv",
        [
            "espn-mcp",
            "--transport",
            "streamable-http",
            "--port",
            "9004",
            "--allowed-host",
            "espn.internal",
            "--allowed-origin",
            "https://espn.com",
        ],
    )
    server.main()
    assert run_args.get("allowed_hosts") == ["espn.internal"]
    assert run_args.get("allowed_origins") == ["https://espn.com"]

    # sse with allowed-host and allowed-origin
    monkeypatch.setattr(
        "sys.argv",
        [
            "espn-mcp",
            "--transport",
            "sse",
            "--port",
            "9005",
            "--allowed-host",
            "espn.internal",
            "--allowed-origin",
            "https://espn.com",
        ],
    )
    server.main()
    assert run_args.get("allowed_hosts") == ["espn.internal"]
    assert run_args.get("allowed_origins") == ["https://espn.com"]

    # wildcard bind on streamable-http without --allowed-host fails closed with parser.error
    monkeypatch.setattr(
        "sys.argv",
        ["espn-mcp", "--transport", "streamable-http", "--host", "0.0.0.0"],
    )
    with pytest.raises(SystemExit):
        server.main()

    # wildcard "*" in --allowed-host fails closed with parser.error
    monkeypatch.setattr(
        "sys.argv",
        ["espn-mcp", "--transport", "streamable-http", "--allowed-host", "*"],
    )
    with pytest.raises(SystemExit):
        server.main()


def test_streamable_http_app_allowed_hosts_dynamic_port(monkeypatch):
    """Verify _streamable_http_app uses dynamic port binding for default allowed_hosts."""
    captured_kwargs = {}

    def fake_http_app(**kwargs):
        captured_kwargs.update(kwargs)
        return MagicMock()

    monkeypatch.setattr(server.mcp, "http_app", fake_http_app)
    server.mcp.streamable_http_app(host="0.0.0.0", port=9999)
    assert captured_kwargs["allowed_hosts"] == [
        "0.0.0.0",
        "localhost",
        "0.0.0.0:9999",
        "localhost:9999",
    ]

    captured_kwargs.clear()
    server.mcp.streamable_http_app(allowed_hosts=["explicit.domain"])
    assert captured_kwargs["allowed_hosts"] == ["explicit.domain"]


def test_handle_shutdown():
    """Verify graceful process exit on shutdown signals."""
    with pytest.raises(SystemExit) as exc_info:
        server._handle_shutdown(15, None)
    assert exc_info.value.code == 0


@pytest.mark.asyncio
async def test_server_streamable_http_dispatch(mock_transport, monkeypatch):
    """Verify Streamable HTTP ASGI app dispatches tool requests with MockTransport backend."""
    async with httpx.AsyncClient(
        transport=mock_transport, base_url="https://site.web.api.espn.com"
    ) as async_client:
        monkeypatch.setattr(server, "client", server.ESPNClient(http_client=async_client))
        app = server.mcp.streamable_http_app(stateless_http=True, json_response=True)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
            ) as http_c:
                meta = {
                    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                    "io.modelcontextprotocol/clientCapabilities": {},
                }
                call_payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "games_get_scoreboard",
                        "arguments": {"sport": "baseball", "league": "mlb", "date": "20260904"},
                        "_meta": meta,
                    },
                }
                res = await http_c.post(
                    "/mcp",
                    json=call_payload,
                    headers={
                        "Content-Type": "application/json",
                        "MCP-Protocol-Version": "2026-07-28",
                        "Mcp-Method": "tools/call",
                        "Mcp-Name": "games_get_scoreboard",
                    },
                )
                assert res.status_code == 200
                data = res.json()
                assert "result" in data
                assert "content" in data["result"]
