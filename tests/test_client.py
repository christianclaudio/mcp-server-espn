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
    """Verify alias resolution and input normalization for supported sports and leagues."""
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
    """Verify scoreboard formatting handles empty and non-standard competitor lists."""
    client = ESPNClient()
    res = client._format_scoreboard(
        {"events": [{"competitions": [{"competitors": []}]}]}, "baseball", "mlb"
    )
    assert res["count"] == 1
    assert res["events"][0]["home_team"] == {}
    assert res["events"][0]["away_team"] == {}

    # Competitor with non-home / non-away role
    res_neutral = client._format_scoreboard(
        {"events": [{"competitions": [{"competitors": [{"homeAway": "neutral"}]}]}]},
        "baseball",
        "mlb",
    )
    assert res_neutral["events"][0]["home_team"] == {}
    assert res_neutral["events"][0]["away_team"] == {}


def test_format_scoreboard_malformed_container_types():
    """Verify scoreboard formatting survives non-list records and probables mappings."""
    client = ESPNClient()
    res = client._format_scoreboard(
        {
            "events": [
                {
                    "competitions": [
                        {
                            "competitors": [
                                {
                                    "homeAway": "home",
                                    "records": {"unexpected": "dict"},
                                    "probables": {"unexpected": "dict"},
                                    "team": {"displayName": "Team A"},
                                }
                            ]
                        }
                    ]
                }
            ]
        },
        "baseball",
        "mlb",
    )
    assert res["events"][0]["home_team"]["record"] == ""
    assert res["events"][0]["home_team"]["probable_starter"] is None


def test_format_game_summary_malformed_odds():
    """Verify game summary formatting survives non-dict awayTeamOdds/homeTeamOdds."""
    client = ESPNClient()
    res = client._format_game_summary(
        {
            "pickcenter": [
                {
                    "provider": {"name": "TestProvider"},
                    "awayTeamOdds": "invalid_string",
                    "homeTeamOdds": 123,
                }
            ]
        },
        "baseball",
        "mlb",
        "1",
    )
    assert len(res["betting_lines"]) == 1
    assert res["betting_lines"][0]["away_moneyline"] is None
    assert res["betting_lines"][0]["home_moneyline"] is None


@pytest.mark.asyncio
async def test_client_request_success(mock_transport):
    """Verify successful GET requests and empty payload responses."""
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
    """Verify path traversal prevention in sanitize_path_param."""
    client = ESPNClient()
    sanitized = client.sanitize_path_param("../../../etc/passwd")
    assert ".." not in sanitized
    assert "%2E%2E" in sanitized or "%2F" in sanitized


@pytest.mark.asyncio
async def test_client_domain_methods(mock_transport):
    """Verify high-level domain client query methods against mocked ESPN responses."""
    async with httpx.AsyncClient(
        transport=mock_transport, base_url="https://site.web.api.espn.com"
    ) as async_client:
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

        await client.close()


@pytest.mark.asyncio
async def test_client_depth_chart_list_format():
    """Verify depth chart parsing when response structure uses list of formations."""

    def depth_list_transport(request: httpx.Request) -> httpx.Response:
        """Mock transport returning list-based depth chart payload."""
        return httpx.Response(
            200,
            json={
                "depthchart": [
                    {
                        "name": "Offense",
                        "positions": {
                            "qb": {
                                "athletes": [
                                    {
                                        "slot": 1,
                                        "rank": 1,
                                        "athlete": {
                                            "id": "123",
                                            "displayName": "Brock Purdy",
                                            "jersey": "13",
                                        },
                                    }
                                ]
                            }
                        },
                    }
                ]
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(depth_list_transport),
        base_url="https://site.web.api.espn.com",
    ) as async_client:
        client = ESPNClient(http_client=async_client)
        res = await client.get_team_depth_chart("football", "nfl", "25")
        assert len(res["positions"]) == 1
        assert res["positions"][0]["formation"] == "Offense"
        assert res["positions"][0]["position"] == "QB"
        assert res["positions"][0]["depth"][0]["name"] == "Brock Purdy"
        await client.close()


@pytest.mark.asyncio
async def test_client_errors():
    """Verify HTTP status code mappings to domain ESPN exception classes."""

    def error_transport(request: httpx.Request) -> httpx.Response:
        """Mock transport returning varying HTTP error codes."""
        url = str(request.url)
        if "400" in url:
            return httpx.Response(400, text="Bad Request")
        if "404" in url:
            return httpx.Response(404, text="Not Found")
        if "429" in url:
            return httpx.Response(429, text="Rate Limited")
        if "500" in url:
            return httpx.Response(500, text="Server Error")
        return httpx.Response(200)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(error_transport), base_url="https://site.web.api.espn.com"
    ) as async_client:
        client = ESPNClient(max_retries=1, http_client=async_client)

        with pytest.raises(ESPNValidationError):
            await client.request("GET", "400")

        with pytest.raises(ESPNNotFoundError):
            await client.request("GET", "404")

        with pytest.raises(ESPNRateLimitError):
            await client.request("GET", "429")

        with pytest.raises(ESPNConnectionError):
            await client.request("GET", "500")

        await client.close()


@pytest.mark.asyncio
async def test_client_network_error():
    """Verify connection failures raise ESPNConnectionError."""

    def fail_transport(request: httpx.Request) -> httpx.Response:
        """Mock transport raising connect error."""
        raise httpx.ConnectError("Connection failed")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(fail_transport), base_url="https://site.web.api.espn.com"
    ) as async_client:
        client = ESPNClient(max_retries=1, http_client=async_client)

        with pytest.raises(ESPNConnectionError) as exc_info:
            await client.request("GET", "network-fail")
        assert "Request failed" in str(exc_info.value)

        await client.close()


@pytest.mark.asyncio
async def test_client_lifecycle():
    """Verify AsyncClient initialization, connection pool reuse, and teardown."""
    from espn_mcp import __version__

    client = ESPNClient()
    c = await client.get_client()
    assert c is not None
    assert f"mcp-server-espn/{__version__}" in c.headers.get("User-Agent", "")

    # Re-using open client exercises client reuse branch (115->126)
    c_reuse = await client.get_client()
    assert c_reuse is c

    await client.close()


@pytest.mark.asyncio
async def test_client_depth_chart_edge_cases():
    """Verify defensive fallbacks for non-dict depth chart formations and positions."""

    def depth_edge_transport(request: httpx.Request) -> httpx.Response:
        """Mock transport returning edge case depth chart structures."""
        url = str(request.url)
        if "invalid-pos-list" in url:
            return httpx.Response(
                200,
                json={
                    "depthchart": [
                        {
                            "name": "Offense",
                            "positions": {"qb": "invalid_non_dict"},
                        }
                    ]
                },
            )
        if "invalid-pos-dict" in url:
            return httpx.Response(
                200,
                json={
                    "depthchart": {
                        "qb": "invalid_non_dict",
                    }
                },
            )
        # depthchart is neither list nor dict (hits 725->742)
        return httpx.Response(200, json={"depthchart": None})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(depth_edge_transport),
        base_url="https://site.web.api.espn.com",
    ) as async_client:
        client = ESPNClient(http_client=async_client)

        # 1. list format with non-dict pos_val (hits 706->718)
        res_list = await client.get_team_depth_chart("football", "nfl", "invalid-pos-list")
        assert len(res_list["positions"]) == 1
        assert res_list["positions"][0]["depth"] == []

        # 2. dict format with non-dict pos_val (hits 728->740)
        res_dict = await client.get_team_depth_chart("football", "nfl", "invalid-pos-dict")
        assert len(res_dict["positions"]) == 1
        assert res_dict["positions"][0]["depth"] == []

        # 3. neither list nor dict (hits 725->742)
        res_none = await client.get_team_depth_chart("football", "nfl", "other")
        assert res_none["positions"] == []

        await client.close()
