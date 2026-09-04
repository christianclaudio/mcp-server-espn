"""Tests for async ESPN HTTP client functionality, alias normalization, and domain methods."""

import httpx
import pytest

from espn_mcp.client import ESPNClient, normalize_sport_league
from espn_mcp.errors import (
    ESPNConnectionError,
    ESPNNotFoundError,
    ESPNRateLimitError,
    ESPNValidationError,
)


def test_normalize_sport_league():
    # League alias
    sport, league = normalize_sport_league("", "mlb")
    assert sport == "baseball" and league == "mlb"

    # Sport alias
    sport, league = normalize_sport_league("nfl", "")
    assert sport == "football" and league == "nfl"

    # CFB / NCAAF alias
    sport, league = normalize_sport_league("", "cfb")
    assert sport == "football" and league == "college-football"

    # Direct valid sport and custom league
    sport, league = normalize_sport_league("baseball", "kbo")
    assert sport == "baseball" and league == "kbo"

    # Direct valid sport and league
    sport, league = normalize_sport_league("baseball", "mlb")
    assert sport == "baseball" and league == "mlb"

    # Invalid throws ESPNValidationError
    with pytest.raises(ESPNValidationError):
        normalize_sport_league("quidditch", "unknown")


def test_format_scoreboard_empty_competitors():
    client = ESPNClient()
    res = client._format_scoreboard(
        {"events": [{"competitions": [{"competitors": []}]}]}, "baseball", "mlb"
    )
    assert res["count"] == 1
    assert res["events"][0]["home_team"] == {}
    assert res["events"][0]["away_team"] == {}


@pytest.mark.asyncio
async def test_client_request_success(mock_transport):
    async_client = httpx.AsyncClient(
        transport=mock_transport, base_url="https://site.web.api.espn.com"
    )
    client = ESPNClient(http_client=async_client)

    res = await client.request("GET", "scoreboard")
    assert "events" in res

    empty_res = await client.request("GET", "empty")
    assert empty_res == {}

    await client.close()


@pytest.mark.asyncio
async def test_client_path_sanitization():
    client = ESPNClient()
    sanitized = client.sanitize_path_param("../../../etc/passwd")
    assert ".." not in sanitized
    assert "%2E%2E" in sanitized or "%2F" in sanitized


@pytest.mark.asyncio
async def test_client_domain_methods(mock_transport):
    async_client = httpx.AsyncClient(
        transport=mock_transport, base_url="https://site.web.api.espn.com"
    )
    client = ESPNClient(http_client=async_client)

    # 1. Scoreboard
    sb = await client.get_scoreboard(
        "baseball", "mlb", dates="2026-09-04", week=1, season_type=2, group="80"
    )
    assert sb["count"] == 1

    ev = sb["events"][0]
    assert ev["event_id"] == "401816789"
    assert ev["home_team"]["abbreviation"] == "PIT"
    assert ev["home_team"]["probable_starter"] == "Paul Skenes"

    # 2. Game Summary
    sum_data = await client.get_game_summary("baseball", "mlb", "401816789")
    assert len(sum_data["betting_lines"]) == 1
    assert sum_data["betting_lines"][0]["provider"] == "DraftKings"
    assert sum_data["predictor"]["homeTeam"]["gameProjection"] == "62.4"
    assert len(sum_data["team_statistics"]) == 1

    # 3. Player Stats
    p_stats = await client.get_player_stats("baseball", "mlb", "401816789")
    assert len(p_stats["player_boxscores"]) == 1
    cats = p_stats["player_boxscores"][0]["categories"]
    assert cats[0]["category"] == "batting"
    assert cats[0]["athletes"][0]["name"] == "Bryan Reynolds"

    # 4. Standings
    standings = await client.get_standings("baseball", "mlb", season=2026)
    assert standings["count"] == 1
    assert standings["standings"][0]["abbreviation"] == "PIT"

    # 5. News
    news = await client.get_news("baseball", "mlb", limit=5)
    assert news["count"] == 1
    assert "Skenes" in news["articles"][0]["headline"]

    # 6. Rankings
    rankings = await client.get_rankings("football", "college-football")
    assert len(rankings["polls"]) == 1
    assert rankings["polls"][0]["ranks"][0]["team"]["abbreviation"] == "ALA"

    # 7. Team Roster
    roster = await client.get_team_roster("baseball", "mlb", "23")
    assert roster["count"] == 1
    assert roster["athletes"][0]["name"] == "Paul Skenes"

    # 8. Depth Chart
    depth = await client.get_team_depth_chart("football", "nfl", "26")
    assert len(depth["positions"]) == 1
    assert depth["positions"][0]["position"] == "QB"

    # 9. Schedule
    sched = await client.get_team_schedule("baseball", "mlb", "23", season=2026)
    assert sched["count"] == 1
    assert sched["games"][0]["matchup"] == "SF at PIT"

    # 10. Athlete Overview
    ath = await client.get_athlete_overview("baseball", "mlb", "5000")
    assert "statistics" in ath
    assert ath["rotowire_notes"][0]["headline"] == "Scheduled to start Friday"


@pytest.mark.asyncio
async def test_client_errors():
    def error_transport(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "404" in url:
            return httpx.Response(404, text="Not Found")
        if "429" in url:
            return httpx.Response(429, text="Rate Limited")
        if "500" in url:
            return httpx.Response(500, text="Server Error")
        return httpx.Response(200)

    async_client = httpx.AsyncClient(
        transport=httpx.MockTransport(error_transport), base_url="https://site.web.api.espn.com"
    )
    client = ESPNClient(max_retries=1, http_client=async_client)

    with pytest.raises(ESPNNotFoundError):
        await client.request("GET", "404")

    with pytest.raises(ESPNRateLimitError):
        await client.request("GET", "429")

    with pytest.raises(ESPNConnectionError):
        await client.request("GET", "500")


@pytest.mark.asyncio
async def test_client_network_error():
    def fail_transport(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection failed")

    async_client = httpx.AsyncClient(
        transport=httpx.MockTransport(fail_transport), base_url="https://site.web.api.espn.com"
    )
    client = ESPNClient(max_retries=1, http_client=async_client)

    with pytest.raises(ESPNConnectionError) as exc_info:
        await client.request("GET", "network-fail")
    assert "Request failed" in str(exc_info.value)


@pytest.mark.asyncio
async def test_client_lifecycle():
    client = ESPNClient()
    c = await client.get_client()
    assert c is not None
    assert "mcp-server-espn/1.0.0" in c.headers.get("User-Agent", "")
    await client.close()
