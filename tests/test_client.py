"""Tests for async ESPN HTTP client functionality, alias normalization, and domain methods."""

import asyncio
import socket
import threading
import urllib.parse
from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from espn_mcp.client import ESPNClient, _validate_base_url, normalize_sport_league
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
        assert len(sum_data["leaders"]) == 1
        assert sum_data["leaders"][0]["category"] == "homeRuns"
        assert len(sum_data["scoring_plays"]) == 1
        assert sum_data["scoring_plays"][0]["type"] == "Home Run"
        assert sum_data["drives"]["current"]["description"] == "Punt"
        assert len(sum_data["against_the_spread"]) == 1
        assert sum_data["against_the_spread"][0]["favorite"] is True

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
        assert len(roster["coach"]) == 1
        assert roster["coach"][0]["name"] == "Derek Shelton"

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

        # 11. Search
        srch = await client.search("Stafford", type="player", limit=5)
        assert srch["count"] == 1
        assert srch["items"][0]["name"] == "Matthew Stafford"

        # 12. List Teams
        teams = await client.list_teams("football", "nfl")
        assert teams["count"] == 1
        assert teams["teams"][0]["id"] == "14"

        # 13. Get Team Detail
        team_detail = await client.get_team("football", "nfl", "14")
        assert team_detail["name"] == "Los Angeles Rams"
        assert team_detail["venue"] == "SoFi Stadium"
        assert team_detail["record"] == "10-7"
        assert team_detail["next_event"]["name"] == "NYG @ LAR"

        # 14. Get Team Statistics
        team_stats = await client.get_team_statistics("football", "nfl", "14")
        assert len(team_stats["team_stats"]) == 1
        assert team_stats["team_stats"][0]["name"] == "passing"
        assert len(team_stats["opponent_stats"]) == 1

        # 15. Get Transactions
        tx = await client.get_transactions("football", "nfl", limit=25)
        assert tx["count"] == 1
        assert tx["transactions"][0]["team_name"] == "Los Angeles Rams"

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


def test_validate_base_url_ssrf_protections() -> None:
    """Verify base URL validation protects against SSRF and non-global destinations."""
    # Default fallback
    assert _validate_base_url("") == "https://site.web.api.espn.com"

    # HTTPS enforcement
    with pytest.raises(ValueError, match="Only HTTPS is permitted"):
        _validate_base_url("http://site.web.api.espn.com")

    # Missing hostname
    with pytest.raises(ValueError, match="missing hostname"):
        _validate_base_url("https://")

    # Internal/loopback hostnames
    for host in ["localhost", "127.0.0.1", "[::1]", "service.local", "db.internal"]:
        with pytest.raises(ValueError, match="Blocked internal/loopback"):
            _validate_base_url(f"https://{host}")

    # Private IP literals
    for ip in ["10.0.0.1", "172.16.0.1", "192.168.1.1", "169.254.169.254", "127.0.0.2"]:
        with pytest.raises(ValueError, match="Blocked private/reserved"):
            _validate_base_url(f"https://{ip}")

    # Valid globally routable public IP
    assert _validate_base_url("https://93.184.216.34") == "https://93.184.216.34"

    # RFC 2606 example domain
    assert _validate_base_url("https://example.com") == "https://example.com"
    assert _validate_base_url("https://api.example.com") == "https://api.example.com"

    # Allowed hosts check
    with pytest.raises(ValueError, match="not in allowed hosts allowlist"):
        _validate_base_url("https://untrusted.espn.com", allowed_hosts_str="site.web.api.espn.com")
    assert (
        _validate_base_url(
            "https://site.web.api.espn.com", allowed_hosts_str="site.web.api.espn.com"
        )
        == "https://site.web.api.espn.com"
    )

    # Valid global domain name via DNS resolution
    with patch(
        "socket.getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
    ):
        assert (
            _validate_base_url("https://api.customdomain.org", check_dns=True)
            == "https://api.customdomain.org"
        )

    # DNS resolving to private IP
    with patch(
        "socket.getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443))],
    ):
        with pytest.raises(ValueError, match="resolving to private/reserved IP"):
            _validate_base_url("https://malicious-rebinding.com", check_dns=True)

    # DNS resolution failure (fail-closed)
    with patch("socket.getaddrinfo", side_effect=socket.gaierror("Name or service not known")):
        with pytest.raises(ValueError, match="Could not resolve hostname in base URL"):
            _validate_base_url("https://unresolvable-domain.com", check_dns=True)

    # Private IP literal in _validate_hostname_dns
    from espn_mcp.client import _validate_hostname_dns

    with pytest.raises(ValueError, match="Blocked private/reserved"):
        _validate_hostname_dns("10.0.0.1")

    # Example.com and global public IP in _validate_hostname_dns
    _validate_hostname_dns("example.com")
    _validate_hostname_dns("api.example.com")
    _validate_hostname_dns("93.184.216.34")


@pytest.mark.asyncio
async def test_ssrf_safe_async_transport() -> None:
    """Verify SSRFSafeAsyncTransport blocks outbound requests to private/reserved destinations."""
    from espn_mcp.client import SSRFSafeAsyncTransport

    transport = SSRFSafeAsyncTransport()

    # Valid public resolution passes to base transport and offloads to worker thread
    loop_thread = threading.get_ident()
    dns_thread = None

    def fake_getaddrinfo(*args: Any, **kwargs: Any) -> list[Any]:
        """Record the calling thread identifier and return a valid public IPv4 address."""
        nonlocal dns_thread
        dns_thread = threading.get_ident()
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    with patch("socket.getaddrinfo", side_effect=fake_getaddrinfo):
        with patch.object(
            httpx.AsyncHTTPTransport,
            "handle_async_request",
            return_value=httpx.Response(200, json={"ok": True}),
        ):
            req = httpx.Request("GET", "https://api.customdomain.org/data")
            resp = await transport.handle_async_request(req)
            assert resp.status_code == 200
            assert dns_thread is not None
            assert dns_thread != loop_thread

    # Private IP resolution raises ESPNConnectionError at request time
    with patch(
        "socket.getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.1", 443))],
    ):
        req = httpx.Request("GET", "https://malicious.com/data")
        with pytest.raises(ESPNConnectionError, match="SSRF validation blocked request"):
            await transport.handle_async_request(req)


@pytest.mark.asyncio
async def test_client_formatter_edge_cases() -> None:
    """Verify defensive handling of coach dicts and varied search link formats."""
    client = ESPNClient()

    # 1. Coach as single dict
    roster_raw = {
        "coach": {
            "id": "14",
            "firstName": "Sean",
            "lastName": "McVay",
            "experience": 8,
        },
        "athletes": [],
    }
    formatted_roster = client._format_team_roster(roster_raw, "football", "nfl", "14")
    assert len(formatted_roster["coach"]) == 1
    assert formatted_roster["coach"][0]["name"] == "Sean McVay"

    # 2. Coach dict with displayName fallback
    roster_raw_disp = {
        "coach": {
            "id": "15",
            "displayName": "Coach Prime",
        },
        "athletes": [],
    }
    formatted_disp = client._format_team_roster(roster_raw_disp, "football", "cfb", "15")
    assert formatted_disp["coach"][0]["name"] == "Coach Prime"

    # 3. Search with string link and None link
    search_raw = {
        "items": [
            {
                "id": "1",
                "name": "Team A",
                "link": "https://espn.com/team-a",
            },
            {
                "id": "2",
                "name": "Team B",
                "link": None,
            },
        ]
    }
    formatted_search = client._format_search(search_raw, "Team", "team")
    assert len(formatted_search["items"]) == 2
    assert formatted_search["items"][0]["link"] == "https://espn.com/team-a"
    assert formatted_search["items"][1]["link"] is None

    await client.close()


@pytest.mark.asyncio
async def test_client_new_methods(mock_transport) -> None:
    """Verify all 18 expanded client methods execute and format data correctly."""
    async_client = httpx.AsyncClient(
        transport=mock_transport, base_url="https://site.web.api.espn.com"
    )
    client = ESPNClient(http_client=async_client)

    # 1. Athlete bio
    bio = await client.get_athlete_bio("football", "nfl", "12483")
    assert bio["athlete_id"] == "12483"
    assert bio["bio"]["college"]["name"] == "Georgia"

    # 2. Athlete stats
    stats = await client.get_athlete_stats("football", "nfl", "12483", season=2026)
    assert stats["season"] == 2026
    assert "passing" in stats["statistics"]["categories"][0]["name"]

    # 3. Athlete gamelog
    gamelog = await client.get_athlete_gamelog("football", "nfl", "12483", season=2026)
    assert gamelog["count"] == 1
    assert gamelog["games"][0]["id"] == "401872947"

    # 4. Athlete splits
    splits = await client.get_athlete_splits("football", "nfl", "12483")
    assert "home" in splits["splits"]["categories"][0]["name"]

    # 5. Leaders by athlete
    l_ath = await client.get_leaders_by_athlete(
        "football", "nfl", limit=5, category="passing", sort="yards"
    )
    assert l_ath["count"] == 1
    assert l_ath["leaders"][0]["athlete"]["displayName"] == "Matthew Stafford"

    # 6. Leaders by team
    l_team = await client.get_leaders_by_team(
        "football", "nfl", limit=5, category="passing", sort="yards"
    )
    assert l_team["count"] == 1
    assert l_team["leaders"][0]["team"]["displayName"] == "Los Angeles Rams"

    # 7. League groups
    groups = await client.get_league_groups("football", "nfl")
    assert groups["count"] == 1
    assert groups["groups"][0]["name"] == "NFC West"

    # 8. League events
    events = await client.get_league_events("football", "nfl", dates="20260927")
    assert events["dates"] == "20260927"
    assert events["count"] == 1

    # 9. League draft
    draft = await client.get_league_draft("football", "nfl", season=2026)
    assert draft["season"] == 2026
    assert draft["draft"]["year"] == 2026

    # 10. Scoreboard header
    hdr = await client.get_scoreboard_header("football", "nfl")
    assert "sports" in hdr
    assert hdr["sports"][0]["leagues"][0]["events"][0]["id"] == "401872947"

    # 11. Event odds
    odds = await client.get_event_odds("football", "nfl", "401872947")
    assert odds["odds"][0]["provider"]["name"] == "DraftKings"

    # 12. Play by play
    pbp = await client.get_play_by_play("football", "nfl", "401872947", limit=10, page=1)
    assert pbp["count"] == 1
    assert pbp["plays"][0]["scoringPlay"] is True

    # 13. Game situation
    sit = await client.get_game_situation("football", "nfl", "401872947")
    assert sit["situation"]["down"] == 3
    assert sit["situation"]["isRedZone"] is True

    # 14. Win probabilities
    probs = await client.get_win_probabilities("football", "nfl", "401872947")
    assert probs["count"] == 1
    assert probs["probabilities"][0]["homeWinPercentage"] == 0.68

    # 15. Game predictor
    pred = await client.get_game_predictor("football", "nfl", "401872947")
    assert pred["predictor"]["homeTeam"]["gameProjection"] == 65.4

    # 16. Calendar (with and without dates)
    cal1 = await client.get_calendar("football", "nfl")
    assert "dates" in cal1["calendar"]
    cal2 = await client.get_calendar("football", "nfl", dates="20260921")
    assert cal2["dates"] == "20260921"

    # 17. Futures (with explicit and default season)
    fut = await client.get_futures("football", "nfl", season=2026)
    assert fut["season"] == 2026
    assert len(fut["futures"]) == 1
    fut_default = await client.get_futures("football", "nfl")
    assert fut_default["season"] == datetime.now(timezone.utc).year
    assert len(fut_default["futures"]) == 1

    # 18. Power index (with explicit and default season)
    fpi = await client.get_power_index("football", "nfl", season=2026)
    assert fpi["season"] == 2026
    assert fpi["power_index"][0]["rank"] == 4
    fpi_default = await client.get_power_index("football", "nfl")
    assert fpi_default["season"] == datetime.now(timezone.utc).year
    assert fpi_default["power_index"][0]["rank"] == 4

    # 19. Empty items list preservation
    empty_odds = client._format_event_odds({"items": []}, "football", "nfl", "1", "1")
    assert empty_odds["odds"] == []
    empty_fut = client._format_futures({"items": []}, "football", "nfl", 2026)
    assert empty_fut["futures"] == []
    empty_fpi = client._format_power_index({"items": []}, "football", "nfl", 2026)
    assert empty_fpi["power_index"] == []

    await client.close()


def test_extract_id_from_ref() -> None:
    """Verify _extract_id_from_ref extracts ID from dict id and $ref URIs."""
    from espn_mcp.client import _extract_id_from_ref

    assert _extract_id_from_ref("not-a-dict") is None
    assert _extract_id_from_ref({}) is None
    assert _extract_id_from_ref({"id": 123}) == "123"
    assert _extract_id_from_ref({"id": "abc"}) == "abc"
    assert (
        _extract_id_from_ref(
            {"$ref": "http://api.espn.com/v2/sports/football/leagues/nfl/teams/14?lang=en"}
        )
        == "14"
    )
    assert _extract_id_from_ref({"$ref": ""}) is None
    assert _extract_id_from_ref({"$ref": 123}) is None
    assert _extract_id_from_ref({"$ref": "/"}) is None


@pytest.mark.asyncio
async def test_get_calendar_ref_resolution() -> None:
    """Verify get_calendar resolves $ref index by querying calendar/ondays."""
    calls: list[str] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        calls.append(url_str)
        if url_str.endswith("/calendar/ondays"):
            return httpx.Response(200, json={"eventDate": {"dates": ["2026-09-22"]}})
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "$ref": "http://sports.core.api.espn.com/v2/sports/football/leagues/nfl/calendar/ondays"
                    }
                ]
            },
        )

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(
        transport=transport, base_url="https://sports.core.api.espn.com"
    ) as http_client:
        client = ESPNClient(http_client=http_client)
        res = await client.get_calendar("football", "nfl")
        assert len(calls) == 2
        assert res["active_dates"] == ["2026-09-22"]
        assert res["calendar"]["dates"] == ["2026-09-22"]


def test_client_thin_formatter_edge_cases() -> None:
    """Verify defensive formatting handles team-nested leaders, ATS lines, and splits."""
    client = ESPNClient()

    # 1. Game summary leaders (Case A: team nested, with None items and missing fields)
    raw_summary_a: dict[str, Any] = {
        "boxscore": {},
        "leaders": [
            None,
            {
                "team": {"id": "1", "displayName": "New York Yankees", "abbreviation": "NYY"},
                "leaders": [
                    None,
                    {
                        "name": "homeRuns",
                        "displayName": "Home Runs",
                        "leaders": [
                            None,
                            {
                                "displayValue": "54",
                                "value": 54,
                                "athlete": {"id": "123", "displayName": "Aaron Judge"},
                            },
                        ],
                    },
                ],
            },
            {"team": None, "leaders": []},
        ],
        "againstTheSpread": [
            {
                "team": {"id": "1"},
                "records": [{"summary": "80-50"}],
            },
            {
                "team": {"id": "2"},
                "records": [{"displayValue": "70-60"}],
            },
            {
                "team": None,
                "records": [None],
            },
        ],
        "pickcenter": [
            None,
            {
                "spread": -1.5,
                "details": "NYY -1.5",
                "homeTeamOdds": {"teamId": 1, "favorite": True, "moneyLine": -160},
                "awayTeamOdds": {"teamId": "2", "underdog": True, "moneyLine": 140},
            },
        ],
    }
    fmt_a = client._format_game_summary(raw_summary_a, "baseball", "mlb", "100")
    assert len(fmt_a["leaders"]) == 1
    assert fmt_a["leaders"][0]["category"] == "homeRuns"
    assert fmt_a["leaders"][0]["display_name"] == "Home Runs"
    assert fmt_a["leaders"][0]["team_name"] == "New York Yankees"
    assert fmt_a["leaders"][0]["leaders"][0]["name"] == "Aaron Judge"
    assert len(fmt_a["against_the_spread"]) == 3
    assert fmt_a["against_the_spread"][0]["line"] == "NYY -1.5"
    assert fmt_a["against_the_spread"][0]["record"] == "80-50"
    assert fmt_a["against_the_spread"][0]["favorite"] is True
    assert fmt_a["against_the_spread"][1]["line"] == "NYY -1.5"
    assert fmt_a["against_the_spread"][1]["record"] == "70-60"
    assert fmt_a["against_the_spread"][1]["underdog"] is True
    assert fmt_a["against_the_spread"][2]["record"] is None

    # 2. Game summary flat leaders with invalid item
    raw_summary_b: dict[str, Any] = {
        "boxscore": {},
        "leaders": [
            {
                "name": "strikeouts",
                "leaders": [None],
            }
        ],
    }
    fmt_b = client._format_game_summary(raw_summary_b, "baseball", "mlb", "100")
    assert len(fmt_b["leaders"]) == 1
    assert fmt_b["leaders"][0]["leaders"] == []

    # 3. Team detail nextEvent non-dict and completed filtering
    raw_team: dict[str, Any] = {
        "team": {
            "displayName": "Lakers",
            "venue": {"fullName": "Crypto.com Arena"},
            "nextEvent": [
                None,
                {"not_an_id": 1},
                {
                    "id": "201",
                    "name": "Past Game",
                    "competitions": [{"status": {"type": {"completed": True, "state": "post"}}}],
                },
                {
                    "id": "202",
                    "name": "Upcoming Game",
                    "date": "2026-10-01",
                    "competitions": [{"status": {"type": {"completed": False, "state": "pre"}}}],
                },
            ],
        }
    }
    fmt_team = client._format_team_detail(raw_team, "basketball", "nba", "13")
    assert fmt_team["venue"] == "Crypto.com Arena"
    assert fmt_team["next_event"] is not None
    assert fmt_team["next_event"]["id"] == "202"

    # Malformed and terminal nextEvent entries
    # (competitions of None, status.type of None, missing name/date)
    raw_team_malformed_nextevent: dict[str, Any] = {
        "team": {
            "displayName": "Lakers",
            "nextEvent": [
                None,
                {"not_an_id": 1},
                {"id": "missing_name_date"},
                {"id": "missing_name_only", "date": "2026-10-01"},
                {"id": "missing_date_only", "name": "Game Without Date"},
                {
                    "id": "bad_comp_pre_ev_post",
                    "name": "Game Comp Pre Event Post",
                    "date": "2026-09-00",
                    "competitions": [{"status": {"type": {"state": "pre", "completed": False}}}],
                    "status": "post",
                },
                {
                    "id": "bad_comp_post_ev_pre",
                    "name": "Game Comp Post Event Pre",
                    "date": "2026-09-00",
                    "competitions": [{"status": {"type": {"state": "post", "completed": True}}}],
                    "status": "pre",
                },
                {
                    "id": "bad1",
                    "name": "Game 1",
                    "date": "2026-09-01",
                    "competitions": None,
                    "status": "post",
                },
                {
                    "id": "bad2",
                    "name": "Game 2",
                    "date": "2026-09-02",
                    "competitions": [],
                    "status": "post",
                },
                {
                    "id": "bad3",
                    "name": "Game 3",
                    "date": "2026-09-03",
                    "competitions": [None],
                    "status": "post",
                },
                {
                    "id": "bad4",
                    "name": "Game 4",
                    "date": "2026-09-04",
                    "competitions": [{"status": None}],
                    "status": "post",
                },
                {
                    "id": "bad5",
                    "name": "Game 5",
                    "date": "2026-09-05",
                    "competitions": [{"status": {"type": None, "state": "post"}}],
                },
                {
                    "id": "bad6",
                    "name": "Game 6",
                    "date": "2026-09-06",
                    "competitions": [{"status": {"type": {"completed": True}}}],
                },
                {
                    "id": "bad7",
                    "name": "Game 7",
                    "date": "2026-09-07",
                    "competitions": [{"status": {"type": {"detail": "Final/OT"}}}],
                },
                {
                    "id": "bad8",
                    "name": "Game 8",
                    "date": "2026-09-08",
                    "competitions": [{"status": {"type": {"detail": "Postponed"}}}],
                },
                {
                    "id": "bad9",
                    "name": "Game 9",
                    "date": "2026-09-09",
                    "competitions": [{"status": {"state": "suspended"}}],
                    "status": {"type": {"completed": True}},
                },
                {
                    "id": "bad10",
                    "name": "Game 10",
                    "date": "2026-09-10",
                    "status": {"completed": True},
                },
                {
                    "id": "bad_ff",
                    "name": "Game Final Four Completed",
                    "date": "2026-09-11",
                    "competitions": [
                        {
                            "status": {
                                "type": {
                                    "state": "post",
                                    "completed": True,
                                    "detail": "Final Four",
                                }
                            }
                        }
                    ],
                },
                {
                    "id": "good_next",
                    "name": "Lakers at Nuggets (Final Four)",
                    "date": "2026-10-28",
                    "competitions": [
                        {
                            "status": {
                                "type": {
                                    "state": "pre",
                                    "completed": False,
                                    "detail": "Final Four",
                                }
                            }
                        }
                    ],
                },
            ],
        }
    }
    fmt_malformed = client._format_team_detail(
        raw_team_malformed_nextevent, "basketball", "nba", "13"
    )
    assert fmt_malformed["next_event"] is not None
    assert fmt_malformed["next_event"]["id"] == "good_next"
    assert fmt_malformed["next_event"]["name"] == "Lakers at Nuggets (Final Four)"

    # All-bad/completed nextEvent list returns next_event as None
    raw_team_all_bad: dict[str, Any] = {
        "team": {
            "displayName": "Lakers",
            "nextEvent": [
                {"id": "bad1", "name": "Game 1", "date": "2026-09-01", "status": "post"},
            ],
        }
    }
    fmt_all_bad = client._format_team_detail(raw_team_all_bad, "basketball", "nba", "13")
    assert fmt_all_bad["next_event"] is None

    # Null franchise and null venue fallback
    raw_team_null_franchise: dict[str, Any] = {
        "team": {
            "id": "14",
            "displayName": "Rams",
            "venue": None,
            "franchise": None,
        }
    }
    fmt_null_franchise = client._format_team_detail(
        raw_team_null_franchise, "football", "nfl", "14"
    )
    assert fmt_null_franchise["venue"] is None

    # 4. Team statistics with opponent list and dict variations
    raw_stats: dict[str, Any] = {
        "results": {
            "stats": {
                "stats": {"name": "offense", "stats": [{"name": "pts", "displayValue": "110"}]}
            },
            "opponent": [
                {"name": "defense", "stats": [{"name": "opp_pts", "displayValue": "105"}]},
            ],
        }
    }
    fmt_stats = client._format_team_statistics(raw_stats, "basketball", "nba", "13")
    assert len(fmt_stats["team_stats"]) == 1
    assert len(fmt_stats["opponent_stats"]) == 1
    assert fmt_stats["opponent_stats"][0]["name"] == "defense"

    # Invalid container and non-list nested stats
    raw_stats_empty: dict[str, Any] = {"results": {"stats": {"stats": 123}, "opponent": 123}}
    fmt_empty = client._format_team_statistics(raw_stats_empty, "basketball", "nba", "13")
    assert fmt_empty["team_stats"] == []
    assert fmt_empty["opponent_stats"] == []

    # 5. Athlete splits with splitCategories
    raw_splits: dict[str, Any] = {
        "splitCategories": [
            None,
            {
                "name": "location",
                "displayName": "Location",
                "splits": [
                    None,
                    {
                        "name": "home",
                        "displayName": "Home",
                        "abbreviation": "H",
                        "stats": ["300", "3"],
                    },
                ],
            },
        ],
        "labels": ["yards", "touchdowns"],
    }
    fmt_splits = client._format_athlete_splits(raw_splits, "football", "nfl", "123", 2026)
    assert len(fmt_splits["splits"]) == 1
    first_split = fmt_splits["splits"][0]["splits"][0]
    assert first_split["stats"]["yards"] == "300"
    assert first_split["stats"]["touchdowns"] == "3"

    # 6. Leaders by athlete with Core API categories and refs
    raw_ath_leaders: dict[str, Any] = {
        "categories": [
            None,
            {
                "name": "passingYards",
                "displayName": "Passing Yards",
                "abbreviation": "YDS",
                "leaders": [
                    None,
                    {
                        "displayValue": "5000",
                        "value": 5000,
                        "athlete": {"id": "10", "displayName": "Patrick Mahomes"},
                        "team": {"$ref": "http://espn.com/teams/12"},
                    },
                ],
            },
        ]
    }
    fmt_ath_ldr = client._format_leaders_by_athlete(raw_ath_leaders, "football", "nfl")
    assert fmt_ath_ldr["count"] == 1
    assert fmt_ath_ldr["categories"][0]["name"] == "passingYards"
    assert fmt_ath_ldr["categories"][0]["leaders"][0]["athlete_name"] == "Patrick Mahomes"
    assert fmt_ath_ldr["categories"][0]["leaders"][0]["team_id"] == "12"
    assert len(fmt_ath_ldr["leaders"]) == 1

    # 7. Leaders by team with Core API categories and refs
    raw_tm_leaders: dict[str, Any] = {
        "categories": [
            None,
            {
                "name": "totalYards",
                "displayName": "Total Yards",
                "abbreviation": "YDS",
                "leaders": [
                    None,
                    {
                        "displayValue": "6000",
                        "value": 6000,
                        "team": {
                            "$ref": "http://espn.com/teams/12",
                            "displayName": "Kansas City Chiefs",
                        },
                    },
                ],
            },
        ]
    }
    fmt_tm_ldr = client._format_leaders_by_team(raw_tm_leaders, "football", "nfl")
    assert fmt_tm_ldr["count"] == 1
    assert fmt_tm_ldr["categories"][0]["leaders"][0]["team_name"] == "Kansas City Chiefs"
    assert fmt_tm_ldr["categories"][0]["leaders"][0]["team_id"] == "12"
    assert len(fmt_tm_ldr["leaders"]) == 1

    # 8. Calendar format with dates list and date strings
    raw_cal: dict[str, Any] = {
        "dates": ["2026-09-01", "2026-09-02"],
        "startDate": "2026-09-01",
        "endDate": "2026-09-02",
        "sections": [{"name": "regular"}],
    }
    fmt_cal = client._format_calendar(raw_cal, "football", "nfl", "20260901")
    assert fmt_cal["start_date"] == "2026-09-01"
    assert fmt_cal["end_date"] == "2026-09-02"
    assert fmt_cal["active_dates"] == ["2026-09-01", "2026-09-02"]
    assert len(fmt_cal["sections"]) == 1


@pytest.mark.asyncio
async def test_client_thin_formatter_hardening() -> None:
    """Verify hardening for predictor fallback, ATS record, venue, gamelog dict, and power index."""

    # 1. get_game_summary with predictor fallback to get_game_predictor
    def summary_predictor_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "summary" in url_str:
            return httpx.Response(200, json={"header": {}, "predictor": {}})
        if "predictor" in url_str:
            return httpx.Response(
                200,
                json={"homeTeam": {"gameProjection": 72.5, "winPercentage": 72.5}},
            )
        return httpx.Response(404)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(summary_predictor_handler),
        base_url="https://site.api.espn.com",
    ) as hc:
        client = ESPNClient(http_client=hc)
        res = await client.get_game_summary("football", "nfl", "401")
        assert res["predictor"]["homeTeam"]["gameProjection"] == 72.5

    # 1b. get_game_summary with predictor fallback failure handled gracefully
    seen_urls: list[str] = []

    def summary_pred_fail_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        seen_urls.append(url_str)
        if "summary" in url_str:
            return httpx.Response(200, json={"header": {}, "predictor": {}})
        return httpx.Response(500)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(summary_pred_fail_handler),
        base_url="https://site.api.espn.com",
    ) as hc:
        client = ESPNClient(http_client=hc, max_retries=0)
        res = await client.get_game_summary("football", "nfl", "401")
        assert res["predictor"] == {}
        # Non-football/basketball does not attempt predictor fallback
        seen_urls.clear()
        res_bb = await client.get_game_summary("baseball", "mlb", "401")
        assert res_bb["predictor"] == {}
        assert not any("predictor" in u for u in seen_urls)

    # 2. _format_game_summary with winprobability fallback for predictor
    # (percentage, fractional, and tiePercentage), and competitor record for ATS
    client = ESPNClient()
    raw_summary_wp: dict[str, Any] = {
        "header": {
            "competitions": [
                None,
                {
                    "competitors": [
                        None,
                        {
                            "id": "14",
                            "team": {"id": "14"},
                            "records": [{"summary": "2-0"}],
                        },
                        {
                            "id": "15",
                            "team": {"id": "15"},
                            "records": None,
                        },
                    ]
                },
            ]
        },
        "winprobability": [{"homeWinPercentage": 81.4}],
        "againstTheSpread": [
            {
                "team": {"id": "14"},
                "line": "LAR -7",
            },
            {
                "team": {"id": "15"},
                "line": "SF +7",
                "record": "1-1",
            },
        ],
    }
    fmt_wp = client._format_game_summary(raw_summary_wp, "football", "nfl", "401")
    assert fmt_wp["predictor"]["source"] == "winprobability"
    assert fmt_wp["predictor"]["homeTeam"]["winPercentage"] == 81.4
    assert fmt_wp["predictor"]["awayTeam"]["winPercentage"] == 18.6
    assert fmt_wp["against_the_spread"][0]["record"] is None
    assert fmt_wp["against_the_spread"][0]["overall_record"] == "2-0"
    assert fmt_wp["against_the_spread"][1]["record"] == "1-1"
    assert fmt_wp["against_the_spread"][1]["overall_record"] is None

    # Fractional winprobability (0.814 -> 81.4%)
    raw_summary_frac: dict[str, Any] = {
        "winprobability": [{"homeWinPercentage": 0.814}],
    }
    fmt_frac = client._format_game_summary(raw_summary_frac, "football", "nfl", "401")
    assert fmt_frac["predictor"]["source"] == "winprobability"
    assert fmt_frac["predictor"]["homeTeam"]["winPercentage"] == 81.4
    assert fmt_frac["predictor"]["awayTeam"]["winPercentage"] == 18.6

    # Fractional with tie percentage (soccer e.g. 0.45 home, 0.20 tie -> 35.0 away)
    raw_summary_tie: dict[str, Any] = {
        "winprobability": [{"homeWinPercentage": 0.45, "tiePercentage": 0.20}],
    }
    fmt_tie = client._format_game_summary(raw_summary_tie, "soccer", "eng.1", "401")
    assert fmt_tie["predictor"]["homeTeam"]["winPercentage"] == 45.0
    assert fmt_tie["predictor"]["tiePercentage"] == 20.0
    assert fmt_tie["predictor"]["awayTeam"]["winPercentage"] == 35.0

    # 3. get_team next_event schedule fallback
    def team_schedule_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "schedule" in url_str:
            return httpx.Response(
                200,
                json={
                    "events": [
                        None,
                        {"id": "499", "competitions": None},
                        {"id": "498", "competitions": []},
                        {"id": "497", "competitions": [None]},
                        {"id": "496", "competitions": [{"status": None}]},
                        {"id": "495", "competitions": [{"status": {"type": None}}]},
                        {
                            "id": "500",
                            "name": "Rams at Seahawks",
                            "date": "2026-09-21",
                            "competitions": [
                                {
                                    "status": {
                                        "type": {
                                            "state": "pre",
                                            "detail": "Scheduled",
                                            "completed": True,
                                        }
                                    },
                                    "competitors": [
                                        {"id": "14"},
                                        {"id": "26", "team": {"displayName": "Seahawks"}},
                                    ],
                                }
                            ],
                        },
                        {
                            "id": "500b",
                            "name": "Rams at 49ers (Postponed)",
                            "date": "2026-09-24",
                            "competitions": [
                                {
                                    "status": {
                                        "type": {
                                            "state": "postponed",
                                            "detail": "Postponed",
                                            "completed": False,
                                        }
                                    },
                                    "competitors": [
                                        {"id": "14"},
                                        {"id": "25", "team": {"displayName": "49ers"}},
                                    ],
                                }
                            ],
                        },
                        {
                            "id": "500c",
                            "name": "Rams at Cardinals (Final/OT)",
                            "date": "2026-09-25",
                            "competitions": [
                                {
                                    "status": {
                                        "type": {
                                            "state": "post",
                                            "detail": "Final/OT",
                                            "completed": True,
                                        }
                                    },
                                    "competitors": [
                                        {"id": "14"},
                                        {"id": "22", "team": {"displayName": "Cardinals"}},
                                    ],
                                }
                            ],
                        },
                        {
                            "id": "500d",
                            "name": "Rams at Seahawks (Final Four)",
                            "date": "2026-09-26",
                            "competitions": [
                                {
                                    "status": {
                                        "type": {
                                            "state": "post",
                                            "detail": "Final Four",
                                            "completed": True,
                                        }
                                    },
                                    "competitors": [
                                        {"id": "14"},
                                        {"id": "26", "team": {"displayName": "Seahawks"}},
                                    ],
                                }
                            ],
                        },
                        {
                            "id": "501",
                            "name": "Rams at Broncos",
                            "date": "2026-09-28",
                            "competitions": [
                                {
                                    "status": {
                                        "type": {
                                            "state": "pre",
                                            "detail": "Final Four",
                                            "completed": False,
                                        }
                                    },
                                    "competitors": [
                                        None,
                                        {"id": "14"},
                                        {"id": "7", "team": {"displayName": "Broncos"}},
                                    ],
                                }
                            ],
                        },
                    ]
                },
            )
        if "teams/14" in url_str:
            return httpx.Response(
                200,
                json={
                    "team": {
                        "id": "14",
                        "displayName": "Rams",
                        "nextEvent": [
                            {
                                "id": "bad1",
                                "name": "Game 1",
                                "date": "2026-09-20",
                                "competitions": [None],
                                "status": "post",
                            },
                            {
                                "id": "bad2",
                                "name": "Game 2",
                                "date": "2026-09-21",
                                "competitions": [{"status": {"type": None, "state": "post"}}],
                            },
                        ],
                    }
                },
            )
        return httpx.Response(404)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(team_schedule_handler),
        base_url="https://site.api.espn.com",
    ) as hc:
        client = ESPNClient(http_client=hc)
        res_team = await client.get_team("football", "nfl", "14")
        assert res_team["next_event"] is not None
        assert res_team["next_event"]["name"] == "Rams at Broncos"

        # Direct verification of _format_team_schedule null/malformed handling
        fmt_sched_nulls = client._format_team_schedule(
            {
                "events": [
                    None,
                    {"competitions": None},
                    {"id": "1", "competitions": [{"status": {"state": "post"}}]},
                ],
                "team": None,
                "season": None,
            },
            "football",
            "nfl",
            "14",
        )
        assert fmt_sched_nulls["count"] == 1
        assert fmt_sched_nulls["team_name"] is None
        assert fmt_sched_nulls["season"] is None

    # 3b. get_team next_event schedule fallback exception handled gracefully
    def team_sched_fail_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "schedule" in url_str:
            return httpx.Response(500)
        if "teams/14" in url_str:
            return httpx.Response(
                200, json={"team": {"id": "14", "displayName": "Rams", "nextEvent": []}}
            )
        return httpx.Response(404)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(team_sched_fail_handler),
        base_url="https://site.api.espn.com",
    ) as hc:
        client = ESPNClient(http_client=hc, max_retries=0)
        res_team_fail = await client.get_team("football", "nfl", "14")
        assert res_team_fail["next_event"] is None

    # 4. _format_team_detail with string venue and non-dict venue
    raw_t_str_venue: dict[str, Any] = {"team": {"venue": "Memorial Coliseum"}}
    fmt_str_v = client._format_team_detail(raw_t_str_venue, "football", "nfl", "14")
    assert fmt_str_v["venue"] == "Memorial Coliseum"

    raw_t_int_venue: dict[str, Any] = {"team": {"venue": 12345}}
    fmt_int_v = client._format_team_detail(raw_t_int_venue, "football", "nfl", "14")
    assert fmt_int_v["venue"] is None

    # 5. _format_team_statistics with splits list opponent and splits dict opponent
    raw_stats_split_list: dict[str, Any] = {
        "results": {"splits": [{"name": "opponent", "stats": [{"name": "points", "value": "24"}]}]}
    }
    fmt_sp_list = client._format_team_statistics(raw_stats_split_list, "football", "nfl", "14")
    assert len(fmt_sp_list["opponent_stats"]) == 1
    assert fmt_sp_list["opponent_stats"][0]["stats"][0]["display_value"] == "24"

    raw_stats_split_dict: dict[str, Any] = {
        "results": {
            "splits": {
                "opponent": [{"name": "points", "stats": [{"name": "points", "value": "21"}]}]
            }
        }
    }
    fmt_sp_dict = client._format_team_statistics(raw_stats_split_dict, "football", "nfl", "14")
    assert len(fmt_sp_dict["opponent_stats"]) == 1

    raw_stats_direct_items: dict[str, Any] = {
        "results": {
            "categories": [
                {"name": "stat_without_sublist", "displayValue": "100"},
                {"name": "empty_cat"},
            ]
        }
    }
    fmt_direct = client._format_team_statistics(raw_stats_direct_items, "football", "nfl", "14")
    assert fmt_direct["team_stats"][0]["stats"][0]["display_value"] == "100"
    assert fmt_direct["team_stats"][1]["stats"] == []

    fmt_stats_nondict = client._format_team_statistics(
        {"results": "invalid"}, "football", "nfl", "14"
    )
    assert fmt_stats_nondict["team_stats"] == []

    fmt_stats_nosplits = client._format_team_statistics(
        {"results": {"other": "val"}}, "football", "nfl", "14"
    )
    assert fmt_stats_nosplits["team_stats"] == []

    # 6. _format_athlete_gamelog with dict games (events, entries, items, and plain dict)
    fmt_gl_events = client._format_athlete_gamelog(
        {"events": {"w1": {"id": "1"}, "w2": {"id": "2"}}}, "football", "nfl", "10", 2026
    )
    assert fmt_gl_events["count"] == 2

    fmt_gl_nested_events = client._format_athlete_gamelog(
        {"gameLog": {"events": [{"id": "1"}, {"id": "2"}]}}, "football", "nfl", "10", 2026
    )
    assert fmt_gl_nested_events["count"] == 2

    fmt_gl_entries = client._format_athlete_gamelog(
        {"gameLog": {"entries": [{"id": "1"}, {"id": "2"}]}}, "football", "nfl", "10", 2026
    )
    assert fmt_gl_entries["count"] == 2

    fmt_gl_items = client._format_athlete_gamelog(
        {"entries": {"items": [{"id": "1"}, {"id": "2"}, {"id": "3"}]}},
        "football",
        "nfl",
        "10",
        2026,
    )
    assert fmt_gl_items["count"] == 3

    fmt_gl_plain_dict = client._format_athlete_gamelog(
        {"events": {"game1": 1, "game2": 2}}, "football", "nfl", "10", 2026
    )
    assert fmt_gl_plain_dict["count"] == 2

    # 7. _format_athlete_splits with category-specific labels and list-of-lists labels
    raw_splits_cats: dict[str, Any] = {
        "splitCategories": [
            {
                "name": "passing",
                "labels": ["ATT", "CMP", "YDS"],
                "splits": [{"name": "All", "stats": [30, 20, 250]}],
            },
            {
                "name": "rushing",
                "labels": ["CAR", "YDS"],
                "splits": [{"name": "All", "stats": [3, -2]}],
            },
        ],
        "labels": [["ATT", "CMP", "YDS"], ["CAR", "YDS"]],
    }
    fmt_splits_cats = client._format_athlete_splits(raw_splits_cats, "football", "nfl", "10", 2026)
    assert fmt_splits_cats["splits"][0]["splits"][0]["stats"]["YDS"] == 250
    assert fmt_splits_cats["splits"][1]["splits"][0]["stats"]["YDS"] == -2

    # 8. _format_calendar with items date strings and $ref objects in dict/list
    raw_cal_items_str: dict[str, Any] = {"items": ["2026-09-01", "2026-09-02"]}
    fmt_cal_items = client._format_calendar(raw_cal_items_str, "football", "nfl", None)
    assert fmt_cal_items["active_dates"] == ["2026-09-01", "2026-09-02"]

    raw_cal_ref_dict: dict[str, Any] = {
        "items": [
            {"$ref": "http://api.espn.com/calendar/ondays/?lang=en"},
            {"$ref": "http://api.espn.com/calendar/offdays?lang=en"},
        ]
    }
    fmt_cal_ref_d = client._format_calendar(raw_cal_ref_dict, "football", "nfl", None)
    assert fmt_cal_ref_d["calendar"]["items"][0]["type"] == "ondays"
    assert fmt_cal_ref_d["calendar"]["items"][1]["type"] == "offdays"

    raw_cal_ref_list: list[Any] = [
        {"$ref": "http://api.espn.com/calendar/whitelist/?lang=en"},
        {"custom": "value"},
    ]
    fmt_cal_ref_l = client._format_calendar(raw_cal_ref_list, "football", "nfl", None)
    assert fmt_cal_ref_l["calendar"][0]["type"] == "whitelist"
    assert fmt_cal_ref_l["calendar"][1]["custom"] == "value"

    # 8b. Leader formatters with limit smaller than number of categories
    raw_multi_cats: dict[str, Any] = {
        "categories": [
            {
                "name": "passing",
                "leaders": [
                    {"value": 100, "athlete": {"id": "1", "displayName": "QB1"}},
                    {"value": 90, "athlete": {"id": "2", "displayName": "QB2"}},
                ],
            },
            {
                "name": "rushing",
                "leaders": [
                    {"value": 50, "athlete": {"id": "3", "displayName": "RB1"}},
                    {"value": 40, "athlete": {"id": "4", "displayName": "RB2"}},
                ],
            },
        ]
    }
    fmt_limit_cats = client._format_leaders_by_athlete(raw_multi_cats, "football", "nfl", limit=1)
    assert fmt_limit_cats["count"] == 2
    assert len(fmt_limit_cats["categories"]) == 2
    assert len(fmt_limit_cats["categories"][0]["leaders"]) == 1
    assert len(fmt_limit_cats["categories"][1]["leaders"]) == 1

    # 9. get_power_index with team_map resolution from list_teams
    def power_index_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "powerindex" in url_str:
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "rank": 1,
                            "team": {"$ref": "http://api.espn.com/v2/teams/14?lang=en"},
                        }
                    ]
                },
            )
        if "teams" in url_str:
            return httpx.Response(
                200,
                json={
                    "teams": [
                        {
                            "team": {
                                "id": "14",
                                "displayName": "Los Angeles Rams",
                                "abbreviation": "LAR",
                            }
                        }
                    ]
                },
            )
        return httpx.Response(404)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(power_index_handler),
        base_url="https://site.api.espn.com",
    ) as hc:
        client = ESPNClient(http_client=hc)
        res_pi = await client.get_power_index("football", "nfl", 2026)
        assert res_pi["power_index"][0]["team"]["id"] == "14"
        assert res_pi["power_index"][0]["team"]["name"] == "Los Angeles Rams"
        assert res_pi["power_index"][0]["team"]["abbreviation"] == "LAR"

    # 9b. get_power_index list_teams failure handled gracefully
    def power_index_fail_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "powerindex" in url_str:
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "rank": 1,
                            "team": {"$ref": "http://api.espn.com/v2/teams/14?lang=en"},
                        }
                    ]
                },
            )
        return httpx.Response(500)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(power_index_fail_handler),
        base_url="https://site.api.espn.com",
    ) as hc:
        client = ESPNClient(http_client=hc, max_retries=0)
        res_pi_fail = await client.get_power_index("football", "nfl", 2026)
        assert res_pi_fail["power_index"][0]["team"]["id"] == "14"

    # 10. Depth chart jersey extraction with displayJersey, number, rank, slot
    assert ESPNClient._format_depth_slot(0, "not_a_dict") is None
    assert ESPNClient._format_depth_slot(0, None) is None
    raw_depth_jersey: dict[str, Any] = {
        "items": [
            {
                "name": "Offense",
                "positions": {
                    "qb": {
                        "athletes": [
                            {
                                "slot": "1",
                                "rank": 1,
                                "displayJersey": "9",
                                "athlete": {"id": "10", "displayName": "Matthew Stafford"},
                            },
                            {
                                "number": "11",
                                "athlete": {"id": "11", "displayName": "Jimmy Garoppolo"},
                            },
                        ]
                    }
                },
            }
        ]
    }
    fmt_dc = client._format_depth_chart(raw_depth_jersey, "football", "nfl", "14")
    assert fmt_dc["positions"][0]["depth"][0]["jersey"] == "9"
    assert fmt_dc["positions"][0]["depth"][1]["jersey"] == "11"

    # 11. Power index edge cases: None item in items, dict without items, empty raw
    fmt_pi_none_item = client._format_power_index(
        {"items": [None, {"rank": 1}]}, "football", "nfl", 2026
    )
    assert len(fmt_pi_none_item["power_index"]) == 1

    fmt_pi_dict_no_items = client._format_power_index({"rank": 1}, "football", "nfl", 2026)
    assert len(fmt_pi_dict_no_items["power_index"]) == 1

    fmt_pi_empty = client._format_power_index({}, "football", "nfl", 2026)
    assert fmt_pi_empty["power_index"] == {}

    # 12. _safe_enrich cancellation and failure handling
    async def cancel_coro() -> None:
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await client._safe_enrich(cancel_coro())

    async def fail_coro() -> None:
        raise ValueError("simulated enrichment error")

    assert await client._safe_enrich(fail_coro()) is None


@pytest.mark.asyncio
async def test_harden_records_jerseys_venue_splits() -> None:
    """Verify ATS record parsing, depth jersey enrichment, venue fallback, and splits."""
    client = ESPNClient()

    # 1. Athlete splits with multi-category subcategories (passing + rushing)
    raw_splits = {
        "categories": [
            {"name": "passing", "count": 2},
            {"name": "rushing", "count": 2},
        ],
        "labels": ["CMP", "YDS", "CAR", "YDS"],
        "splitCategories": [
            {
                "displayName": "Season",
                "splits": [
                    {
                        "displayName": "2026 Regular Season",
                        "stats": ["37", "482", "6", "-2"],
                    }
                ],
            }
        ],
    }
    fmt_splits = client._format_athlete_splits(raw_splits, "football", "nfl", "12483", 2026)
    season_split = fmt_splits["splits"][0]["splits"][0]
    assert season_split["stats"]["passing"]["YDS"] == "482"
    assert season_split["stats"]["passing"]["CMP"] == "37"
    assert season_split["stats"]["rushing"]["YDS"] == "-2"
    assert season_split["stats"]["rushing"]["CAR"] == "6"

    # 1b. Splits fallback when category count is None (survives without TypeError)
    raw_splits_null_count = {
        "categories": [
            {"name": "passing", "count": None},
            {"name": "rushing", "count": 2},
        ],
        "labels": ["CMP", "YDS"],
        "splitCategories": [
            {
                "displayName": "Season",
                "splits": [
                    {
                        "displayName": "2026 Regular Season",
                        "stats": ["37", "482"],
                    }
                ],
            }
        ],
    }
    fmt_splits_null = client._format_athlete_splits(
        raw_splits_null_count, "football", "nfl", "12483", 2026
    )
    assert fmt_splits_null["splits"][0]["splits"][0]["stats"]["CMP"] == "37"
    assert fmt_splits_null["splits"][0]["splits"][0]["stats"]["YDS"] == "482"

    # 1c. Splits grouping with a zero-count category
    raw_splits_zero_count = {
        "categories": [
            {"name": "passing", "count": 2},
            {"name": "rushing", "count": 0},
        ],
        "labels": ["CMP", "YDS"],
        "splitCategories": [
            {
                "displayName": "Season",
                "splits": [
                    {
                        "displayName": "2026 Regular Season",
                        "stats": ["37", "482"],
                    }
                ],
            }
        ],
    }
    fmt_splits_zero = client._format_athlete_splits(
        raw_splits_zero_count, "football", "nfl", "12483", 2026
    )
    assert fmt_splits_zero["splits"][0]["splits"][0]["stats"]["passing"]["CMP"] == "37"
    assert fmt_splits_zero["splits"][0]["splits"][0]["stats"]["passing"]["YDS"] == "482"
    assert fmt_splits_zero["splits"][0]["splits"][0]["stats"]["rushing"] == {}

    # 1d. Flat mapping disambiguates duplicate labels
    raw_splits_dup_labels = {
        "categories": None,
        "labels": ["YDS", "YDS"],
        "splitCategories": [
            {
                "displayName": "Season",
                "splits": [
                    {
                        "displayName": "2026 Regular Season",
                        "stats": ["482", "-2"],
                    }
                ],
            }
        ],
    }
    fmt_splits_dup = client._format_athlete_splits(
        raw_splits_dup_labels, "football", "nfl", "12483", 2026
    )
    assert fmt_splits_dup["splits"][0]["splits"][0]["stats"]["YDS"] == "482"
    assert fmt_splits_dup["splits"][0]["splits"][0]["stats"]["YDS_1"] == "-2"

    # 1e. Single category grouping under category name
    raw_splits_single_cat = {
        "categories": [{"name": "passing", "count": 2}],
        "labels": ["CMP", "YDS"],
        "splitCategories": [
            {
                "displayName": "Season",
                "splits": [
                    {
                        "displayName": "2026 Regular Season",
                        "stats": ["37", "482"],
                    }
                ],
            }
        ],
    }
    fmt_splits_single = client._format_athlete_splits(
        raw_splits_single_cat, "football", "nfl", "12483", 2026
    )
    assert fmt_splits_single["splits"][0]["splits"][0]["stats"]["passing"]["CMP"] == "37"
    assert fmt_splits_single["splits"][0]["splits"][0]["stats"]["passing"]["YDS"] == "482"

    # 1f. Flat label collision with pre-existing suffix (reserves source labels)
    raw_splits_colliding = {
        "categories": None,
        "labels": ["YDS", "YDS", "YDS_1"],
        "splitCategories": [
            {
                "displayName": "Season",
                "splits": [
                    {
                        "displayName": "2026 Regular Season",
                        "stats": ["100", "200", "300"],
                    }
                ],
            }
        ],
    }
    fmt_splits_colliding = client._format_athlete_splits(
        raw_splits_colliding, "football", "nfl", "12483", 2026
    )
    assert fmt_splits_colliding["splits"][0]["splits"][0]["stats"]["YDS"] == "100"
    assert fmt_splits_colliding["splits"][0]["splits"][0]["stats"]["YDS_2"] == "200"
    assert fmt_splits_colliding["splits"][0]["splits"][0]["stats"]["YDS_1"] == "300"

    # 1g. Grouped subcategory duplicate label collision handling (reserves source labels)
    raw_splits_sub_dup = {
        "categories": [{"name": "passing", "count": 3}],
        "labels": ["YDS", "YDS", "YDS_1"],
        "splitCategories": [
            {
                "displayName": "Season",
                "splits": [
                    {
                        "displayName": "2026 Regular Season",
                        "stats": ["482", "-2", "15"],
                    }
                ],
            }
        ],
    }
    fmt_splits_sub_dup = client._format_athlete_splits(
        raw_splits_sub_dup, "football", "nfl", "12483", 2026
    )
    assert fmt_splits_sub_dup["splits"][0]["splits"][0]["stats"]["passing"]["YDS"] == "482"
    assert fmt_splits_sub_dup["splits"][0]["splits"][0]["stats"]["passing"]["YDS_2"] == "-2"
    assert fmt_splits_sub_dup["splits"][0]["splits"][0]["stats"]["passing"]["YDS_1"] == "15"

    # 1h. Length mismatch between total category count and split row falls back to flat mapping
    raw_splits_mismatch = {
        "categories": [
            {"name": "passing", "count": 2},
            {"name": "rushing", "count": 2},
        ],
        "labels": ["CMP", "YDS", "CAR"],
        "splitCategories": [
            {
                "displayName": "Season",
                "splits": [
                    {
                        "displayName": "2026 Regular Season",
                        "stats": ["37", "482", "6"],
                    }
                ],
            }
        ],
    }
    fmt_splits_mismatch = client._format_athlete_splits(
        raw_splits_mismatch, "football", "nfl", "12483", 2026
    )
    assert fmt_splits_mismatch["splits"][0]["splits"][0]["stats"]["CMP"] == "37"
    assert fmt_splits_mismatch["splits"][0]["splits"][0]["stats"]["YDS"] == "482"
    assert fmt_splits_mismatch["splits"][0]["splits"][0]["stats"]["CAR"] == "6"

    # 1i. Unicode non-ASCII digit or boolean in category count falls back to flat
    # mapping without error
    raw_splits_unicode_count = {
        "categories": [
            {"name": "passing", "count": "²"},
            {"name": "rushing", "count": True},
        ],
        "labels": ["CMP", "YDS"],
        "splitCategories": [
            {
                "displayName": "Season",
                "splits": [
                    {
                        "displayName": "2026 Regular Season",
                        "stats": ["37", "482"],
                    }
                ],
            }
        ],
    }
    fmt_splits_unicode = client._format_athlete_splits(
        raw_splits_unicode_count, "football", "nfl", "12483", 2026
    )
    assert fmt_splits_unicode["splits"][0]["splits"][0]["stats"]["CMP"] == "37"
    assert fmt_splits_unicode["splits"][0]["splits"][0]["stats"]["YDS"] == "482"

    # 2. Team schedule home venue calculation and per-game venue
    raw_sched = {
        "team": {"displayName": "Los Angeles Rams"},
        "season": {"year": 2026},
        "events": [
            {
                "id": "1",
                "name": "Giants at Rams",
                "date": "2026-09-20",
                "competitions": [
                    {
                        "competitors": [
                            {"id": "14", "homeAway": "home"},
                            {"id": "19", "homeAway": "away", "team": {"displayName": "Giants"}},
                        ],
                        "venue": {"fullName": "SoFi Stadium"},
                        "status": {"type": {"state": "post", "completed": True}},
                    }
                ],
            },
            {
                "id": "2",
                "name": "Rams at Broncos",
                "date": "2026-09-27",
                "competitions": [
                    {
                        "competitors": [
                            {"id": "14", "homeAway": "away"},
                            {"id": "7", "homeAway": "home", "team": {"displayName": "Broncos"}},
                        ],
                        "venue": "Empower Field at Mile High",
                        "status": {"type": {"state": "pre", "completed": False}},
                    }
                ],
            },
        ],
    }
    fmt_sched = client._format_team_schedule(raw_sched, "football", "nfl", "14")
    assert fmt_sched["home_venue"] == "SoFi Stadium"
    assert fmt_sched["games"][0]["venue"] == "SoFi Stadium"
    assert fmt_sched["games"][1]["venue"] == "Empower Field at Mile High"

    # 3. get_team with null team.venue falling back to schedule home_venue
    raw_team = {
        "team": {
            "id": "14",
            "displayName": "Los Angeles Rams",
            "venue": None,
            "franchise": {"venue": {"fullName": "Los Angeles Memorial Coliseum"}},
        }
    }
    with (
        patch.object(client, "request", return_value=raw_team),
        patch.object(
            client, "get_team_schedule", return_value={"home_venue": "SoFi Stadium", "games": []}
        ),
    ):
        res_team = await client.get_team("football", "nfl", "14")
        assert res_team["venue"] == "SoFi Stadium"

    # 4. ATS formatting with competitor.record as dict, and ats_item record variations
    raw_summary = {
        "header": {
            "competitions": [
                {
                    "competitors": [
                        {
                            "id": "14",
                            "record": {"summary": "1-1"},
                        },
                        {
                            "id": "19",
                            "record": [{"displayValue": "0-2"}],
                        },
                        {
                            "id": "20",
                            "record": "10-6",
                        },
                        {
                            "id": "21",
                            "record": ["11-5"],
                        },
                    ]
                }
            ]
        },
        "againstTheSpread": [
            {
                "team": {"id": "14"},
                "record": [{"summary": "2-0-0"}],
            },
            {
                "team": {"id": "19"},
                "record": {"displayValue": "1-1-0"},
            },
            {
                "team": {"id": "20"},
                "record": "0-2-0",
            },
            {
                "team": {"id": "21"},
                "records": ["3-1-0"],
            },
            {
                "team": {"id": "22"},
                "record": ["4-0-0"],
            },
        ],
    }
    fmt_sum = client._format_game_summary(raw_summary, "football", "nfl", "401872947")
    ats = fmt_sum["against_the_spread"]
    assert ats[0]["overall_record"] == "1-1"
    assert ats[0]["record"] == "2-0-0"
    assert ats[1]["overall_record"] == "0-2"
    assert ats[1]["record"] == "1-1-0"
    assert ats[2]["overall_record"] == "10-6"
    assert ats[2]["record"] == "0-2-0"
    assert ats[3]["overall_record"] == "11-5"
    assert ats[3]["record"] == "3-1-0"
    assert ats[4]["record"] == "4-0-0"

    # 5. _enrich_ats_record unit testing (standard, fallback non-numeric, empty/None)
    mock_odds_rec = {
        "items": [
            {
                "type": "spreadOverall",
                "stats": [
                    {"displayName": "Wins", "displayValue": "5.0"},
                    {"displayName": "Losses", "displayValue": "9.0"},
                    {"displayName": "Pushes", "displayValue": "2.0"},
                ],
            }
        ]
    }
    with patch.object(client, "request", return_value=mock_odds_rec):
        rec_val = await client._enrich_ats_record("football", "nfl", 2026, 2, "14")
        assert rec_val == "5-9-2"

    mock_odds_non_num = {
        "items": [
            {
                "type": "spreadOverall",
                "stats": [
                    {"displayName": "Wins", "displayValue": "N/A"},
                    {"displayName": "Losses", "displayValue": "N/A"},
                    {"displayName": "Pushes", "displayValue": "N/A"},
                ],
            }
        ]
    }
    with patch.object(client, "request", return_value=mock_odds_non_num):
        rec_val_non_num = await client._enrich_ats_record("football", "nfl", 2026, 2, "14")
        assert rec_val_non_num == "N/A-N/A-N/A"

    with patch.object(client, "request", return_value=None):
        rec_val_none = await client._enrich_ats_record("football", "nfl", 2026, 2, "14")
        assert rec_val_none is None

    with patch.object(client, "request", return_value={"items": "not-a-list"}):
        assert await client._enrich_ats_record("football", "nfl", 2026, 2, "14") is None

    with patch.object(
        client, "request", return_value={"items": [{"type": "spreadOverall", "stats": None}]}
    ):
        assert await client._enrich_ats_record("football", "nfl", 2026, 2, "14") is None

    with patch.object(
        client,
        "request",
        return_value={
            "items": [
                {"type": "spreadOverall", "stats": [{"displayName": "Pushes", "displayValue": "1"}]}
            ]
        },
    ):
        assert await client._enrich_ats_record("football", "nfl", 2026, 2, "14") is None

    # 6. get_game_summary with live-like null ATS record trigger
    raw_summary_enrich = {
        "header": {"season": {"year": 2026, "type": 2}},
        "againstTheSpread": [{"team": {"id": "14"}, "record": None, "records": []}],
    }
    with patch.object(client, "request", return_value=raw_summary_enrich):
        with patch.object(client, "_enrich_ats_record", return_value="5-9-2"):
            sum_res = await client.get_game_summary("football", "nfl", "401872947")
            assert sum_res["against_the_spread"][0]["record"] == "5-9-2"

    raw_summary_bad_season = {
        "header": {"season": {"year": "bad_year", "type": "bad_type"}},
        "againstTheSpread": [{"team": {"id": "14"}, "record": None, "records": []}],
    }
    with patch.object(client, "request", return_value=raw_summary_bad_season):
        sum_bad_season = await client.get_game_summary("football", "nfl", "401872947")
        assert sum_bad_season["against_the_spread"][0]["record"] is None

    # 5. get_team_depth_chart with roster jersey enrichment
    raw_dc = {
        "depthchart": {
            "qb": {
                "athletes": [
                    {"id": "12483", "displayName": "Matthew Stafford"},
                    {"id": "4259553", "displayName": "Stetson Bennett IV"},
                ]
            }
        }
    }
    raw_roster = {
        "athletes": [
            {
                "items": [
                    {"id": "12483", "jersey": "9"},
                    {"id": "4259553", "displayJersey": "13"},
                ]
            }
        ]
    }

    async def mock_req(method: str, path: str) -> Any:
        """Route mock requests for depthcharts and roster."""
        if "depthcharts" in path:
            return raw_dc
        if "roster" in path:
            return raw_roster
        return {}

    with patch.object(client, "request", side_effect=mock_req):
        dc_res = await client.get_team_depth_chart("football", "nfl", "14")
        assert dc_res["positions"][0]["depth"][0]["jersey"] == "9"
        assert dc_res["positions"][0]["depth"][1]["jersey"] == "13"

    await client.close()


@pytest.mark.asyncio
async def test_game_summary_ats_enrichment_transport() -> None:
    """Verify transport-level ATS enrichment request path, host, and response integration."""
    recorded_urls: list[str] = []

    def transport_handler(request: httpx.Request) -> httpx.Response:
        """Mock transport serving game summary and odds-records responses."""
        url_str = str(request.url)
        recorded_urls.append(url_str)
        if "summary" in url_str:
            return httpx.Response(
                200,
                json={
                    "header": {
                        "competitions": [
                            {
                                "competitors": [
                                    {"id": "14", "record": [{"type": "total", "summary": "1-1"}]}
                                ]
                            }
                        ],
                        "season": {"year": 2026, "type": 2},
                    },
                    "againstTheSpread": [
                        {
                            "team": {"id": "14"},
                            "record": None,
                            "records": [],
                        }
                    ],
                },
            )
        if "odds-records" in url_str:
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "type": "spreadOverall",
                            "stats": [
                                {"displayName": "Wins", "displayValue": "5"},
                                {"displayName": "Losses", "displayValue": "9"},
                                {"displayName": "Pushes", "displayValue": "2"},
                            ],
                        }
                    ]
                },
            )
        return httpx.Response(404, json={})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport_handler),
        base_url="https://site.api.espn.com",
    ) as async_client:
        client = ESPNClient(http_client=async_client)
        res = await client.get_game_summary("football", "nfl", "401872947")
        assert res["against_the_spread"][0]["record"] == "5-9-2"
        assert res["against_the_spread"][0]["overall_record"] == "1-1"

        odds_reqs = [u for u in recorded_urls if "odds-records" in u]
        assert len(odds_reqs) == 1
        parsed_url = urllib.parse.urlparse(odds_reqs[0])
        assert parsed_url.netloc == "sports.core.api.espn.com"
        assert (
            parsed_url.path
            == "/v2/sports/football/leagues/nfl/seasons/2026/types/2/teams/14/odds-records"
        )
        await client.close()


@pytest.mark.asyncio
async def test_thin_endpoints_and_null_header_enrichment() -> None:
    """Verify null header handling, leader category mapping, refs,
    and player position enrichment."""
    client = ESPNClient()

    # 1. _format_game_summary with header: None
    raw_summary_null_header = {
        "header": None,
        "boxscore": None,
        "pickcenter": None,
        "predictor": None,
        "winprobability": None,
        "againstTheSpread": [],
    }
    fmt_summary = client._format_game_summary(
        raw_summary_null_header, "football", "nfl", "401872947"
    )
    assert fmt_summary["event_id"] == "401872947"
    assert fmt_summary["header"]["season"] == {}
    assert fmt_summary["betting_lines"] == []

    # 2. get_leaders_by_athlete and get_leaders_by_team category and sort mapping via MockTransport
    captured_requests: list[httpx.Request] = []

    def mock_leaders_transport(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(
            200,
            json={
                "athletes": [
                    {
                        "athlete": {
                            "id": "12483",
                            "displayName": "Matthew Stafford",
                            "position": {"abbreviation": "QB"},
                            "teamId": "14",
                            "teamName": "Los Angeles Rams",
                        },
                        "categories": [
                            {
                                "name": "passing",
                                "displayName": "Passing",
                                "values": [327],
                                "ranks": [1],
                            }
                        ],
                    },
                    None,  # non-dict item
                ],
                "teams": [
                    {
                        "team": {
                            "id": "14",
                            "displayName": "Los Angeles Rams",
                            "abbreviation": "LAR",
                        },
                        "categories": [
                            {
                                "name": "passing",
                                "displayName": "Passing",
                                "values": [327],
                                "ranks": [1],
                            }
                        ],
                    }
                ],
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(mock_leaders_transport),
        base_url="https://site.api.espn.com",
    ) as async_client:
        leaders_client = ESPNClient(http_client=async_client)

        # 2a. NFL mapped category
        res_nfl = await leaders_client.get_leaders_by_athlete("football", "nfl", category="passing")
        assert (
            captured_requests[-1].url.path
            == "/apis/common/v3/sports/football/nfl/statistics/byathlete"
        )
        assert captured_requests[-1].url.params["category"] == "offense"
        assert captured_requests[-1].url.params["sort"] == "passing.passingYards:desc"
        assert len(res_nfl["categories"]) == 1
        assert res_nfl["categories"][0]["athlete"]["displayName"] == "Matthew Stafford"
        assert res_nfl["categories"][0]["categories"][0]["name"] == "passing"

        # 2a-2. NFL mapped category with explicit sort preserved
        await leaders_client.get_leaders_by_athlete(
            "football", "nfl", category="passing", sort="passing.passingTouchdowns:desc"
        )
        assert (
            captured_requests[-1].url.path
            == "/apis/common/v3/sports/football/nfl/statistics/byathlete"
        )
        assert captured_requests[-1].url.params["category"] == "offense"
        assert captured_requests[-1].url.params["sort"] == "passing.passingTouchdowns:desc"

        # 2b. NFL unmapped category
        await leaders_client.get_leaders_by_athlete(
            "football", "nfl", category="custom_stat", sort="custom:desc"
        )
        assert (
            captured_requests[-1].url.path
            == "/apis/common/v3/sports/football/nfl/statistics/byathlete"
        )
        assert captured_requests[-1].url.params["category"] == "custom_stat"
        assert captured_requests[-1].url.params["sort"] == "custom:desc"

        # 2c. Non-NFL sport category
        await leaders_client.get_leaders_by_athlete("baseball", "mlb", category="batting")
        assert (
            captured_requests[-1].url.path
            == "/apis/common/v3/sports/baseball/mlb/statistics/byathlete"
        )
        assert captured_requests[-1].url.params["category"] == "batting"

        # 2d. get_leaders_by_team NFL category mapping
        await leaders_client.get_leaders_by_team("football", "nfl", category="passing")
        assert (
            captured_requests[-1].url.path
            == "/apis/common/v3/sports/football/nfl/statistics/byteam"
        )
        assert captured_requests[-1].url.params["category"] == "offense"
        assert captured_requests[-1].url.params["sort"] == "passing.passingYards:desc"

        await leaders_client.get_leaders_by_team(
            "football", "nfl", category="passing", sort="passing.passingTouchdowns:desc"
        )
        assert (
            captured_requests[-1].url.path
            == "/apis/common/v3/sports/football/nfl/statistics/byteam"
        )
        assert captured_requests[-1].url.params["category"] == "offense"
        assert captured_requests[-1].url.params["sort"] == "passing.passingTouchdowns:desc"

        await leaders_client.get_leaders_by_team(
            "football", "nfl", category="custom_stat", sort="custom:desc"
        )
        assert (
            captured_requests[-1].url.path
            == "/apis/common/v3/sports/football/nfl/statistics/byteam"
        )
        assert captured_requests[-1].url.params["category"] == "custom_stat"
        assert captured_requests[-1].url.params["sort"] == "custom:desc"

        await leaders_client.get_leaders_by_team("baseball", "mlb", category="fielding")
        assert (
            captured_requests[-1].url.path
            == "/apis/common/v3/sports/baseball/mlb/statistics/byteam"
        )
        assert captured_requests[-1].url.params["category"] == "fielding"

        await leaders_client.close()

    # 3. _format_leaders_by_team with fallback categories and non-dict item
    raw_team_leaders = {
        "teams": [
            {
                "team": {
                    "id": "14",
                    "displayName": "Los Angeles Rams",
                    "abbreviation": "LAR",
                },
                "categories": [
                    {
                        "name": "totalYards",
                        "displayName": "Total Yards",
                        "values": [450],
                        "ranks": [2],
                    }
                ],
            },
            None,
        ]
    }
    fmt_team = client._format_leaders_by_team(raw_team_leaders, "football", "nfl")
    assert fmt_team["count"] == 1
    assert fmt_team["categories"][0]["team"]["abbreviation"] == "LAR"
    assert fmt_team["categories"][0]["categories"][0]["name"] == "totalYards"

    # 4. _format_calendar with non-dict and seasonType $ref
    raw_calendar = {
        "eventDate": {"dates": ["2026-09-20"]},
        "sections": [
            None,
            {
                "label": "Regular Season",
                "seasonType": {
                    "$ref": "http://sports.core.api.espn.com/v2/sports/football/leagues/nfl/seasons/2026/types/2?lang=en"
                },
            },
        ],
    }
    fmt_cal = client._format_calendar(raw_calendar, "football", "nfl", "2026-09-20")
    assert fmt_cal["sections"][0]["seasonType"]["id"] == "2"

    # 5. _format_futures with athlete and team $ref resolution and list/dict fallbacks
    raw_futures = {
        "items": [
            None,
            {
                "id": 100,
                "name": "Super Bowl Champion",
                "futures": [
                    None,
                    {
                        "provider": {"name": "DraftKings"},
                        "books": [
                            None,
                            {
                                "value": "+500",
                                "athlete": {
                                    "$ref": "http://sports.core.api.espn.com/v2/sports/football/leagues/nfl/seasons/2026/athletes/12483?lang=en"
                                },
                                "team": {
                                    "$ref": "http://sports.core.api.espn.com/v2/sports/football/leagues/nfl/seasons/2026/teams/14?lang=en"
                                },
                            },
                        ],
                    },
                ],
            },
        ]
    }
    fmt_fut = client._format_futures(raw_futures, "football", "nfl", 2026)
    book = fmt_fut["futures"][0]["futures"][0]["books"][0]
    assert book["athlete"]["id"] == "12483"
    assert book["athlete_id"] == "12483"
    assert book["team"]["id"] == "14"
    assert book["team_id"] == "14"

    # 5b. _format_futures single dict fallback
    fmt_fut_dict = client._format_futures({"name": "MVP"}, "football", "nfl", 2026)
    assert fmt_fut_dict["futures"][0]["name"] == "MVP"
    fmt_fut_empty = client._format_futures({}, "football", "nfl", 2026)
    assert fmt_fut_empty["futures"] == {}
    fmt_fut_none = client._format_futures(None, "football", "nfl", 2026)  # type: ignore[arg-type]
    assert fmt_fut_none["futures"] is None

    # 5c. Leaders fallback with mixed types, limit filtering, and links/logos pruning
    raw_ath_fb = {
        "athletes": [
            None,
            "invalid",
            {
                "athlete": {"id": "1", "displayName": "Athlete 1"},
                "links": [{"href": "http://example.com"}],
                "logos": [{"href": "http://example.com/logo.png"}],
            },
            {"athlete": {"id": "2", "displayName": "Athlete 2"}},
        ]
    }
    fmt_ath_fb = client._format_leaders_by_athlete(raw_ath_fb, "football", "nfl", limit=2)
    assert len(fmt_ath_fb["leaders"]) == 2
    assert "links" not in fmt_ath_fb["leaders"][0]
    assert "logos" not in fmt_ath_fb["leaders"][0]

    raw_team_fb = {
        "teams": [
            None,
            "invalid",
            {
                "team": {"id": "1", "displayName": "Team 1"},
                "links": [{"href": "http://example.com"}],
                "logos": [{"href": "http://example.com/logo.png"}],
            },
            {"team": {"id": "2", "displayName": "Team 2"}},
        ]
    }
    fmt_team_fb = client._format_leaders_by_team(raw_team_fb, "football", "nfl", limit=2)
    assert len(fmt_team_fb["leaders"]) == 2
    assert "links" not in fmt_team_fb["leaders"][0]
    assert "logos" not in fmt_team_fb["leaders"][0]

    # 6. _format_player_stats with position_map and non-dict items
    raw_pstats = {
        "boxscore": {
            "players": [
                None,
                {
                    "team": {"id": "14", "displayName": "Rams"},
                    "statistics": [
                        None,
                        {
                            "type": "passing",
                            "labels": ["C/ATT", "YDS"],
                            "athletes": [
                                None,
                                {
                                    "athlete": {
                                        "id": "12483",
                                        "displayName": "Matthew Stafford",
                                        "jersey": "9",
                                        "position": None,
                                    },
                                    "stats": ["22/31", "327"],
                                },
                            ],
                        },
                    ],
                },
            ]
        }
    }
    fmt_ps = client._format_player_stats(
        raw_pstats, "football", "nfl", "401872947", position_map={"12483": "QB"}
    )
    ath_box = fmt_ps["player_boxscores"][0]["categories"][0]["athletes"][0]
    assert ath_box["name"] == "Matthew Stafford"
    assert ath_box["position"] == "QB"
    assert ath_box["stats"]["YDS"] == "327"

    # 6b. _format_player_stats with non-dict team and non-list containers
    fmt_ps_malformed = client._format_player_stats(
        {
            "boxscore": {
                "players": [
                    {
                        "team": None,
                        "statistics": "invalid_not_a_list",
                    },
                    {
                        "team": "not_a_dict",
                        "statistics": [
                            {
                                "type": "rushing",
                                "labels": ["CAR"],
                                "athletes": "not_a_list",
                            }
                        ],
                    },
                ]
            }
        },
        "football",
        "nfl",
        "401872947",
    )
    assert len(fmt_ps_malformed["player_boxscores"]) == 2
    assert fmt_ps_malformed["player_boxscores"][0]["team_id"] is None

    # 7. get_player_stats end-to-end transport enrichment
    def pstats_transport(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "summary" in url_str:
            return httpx.Response(
                200,
                json={
                    "boxscore": {
                        "players": [
                            None,
                            {"team": None},
                            {
                                "team": {"id": "14"},
                                "statistics": [
                                    None,
                                    {
                                        "type": "passing",
                                        "labels": ["YDS"],
                                        "athletes": [
                                            {
                                                "athlete": {
                                                    "id": "12483",
                                                    "displayName": "Matthew Stafford",
                                                },
                                                "stats": ["327"],
                                            }
                                        ],
                                    },
                                ],
                            },
                            {"team": {"id": "14"}},
                        ]
                    }
                },
            )
        if "teams/14/roster" in url_str:
            return httpx.Response(
                200,
                json={
                    "athletes": [
                        {
                            "items": [
                                {
                                    "id": "12483",
                                    "position": {"abbreviation": "QB"},
                                },
                                {
                                    "id": "99991",
                                    "position": "RB",
                                },
                                {
                                    "id": "99992",
                                    "position": {},
                                },
                                {
                                    "id": "99993",
                                    "position": None,
                                },
                            ]
                        }
                    ]
                },
            )
        return httpx.Response(404, json={})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(pstats_transport),
        base_url="https://site.api.espn.com",
    ) as async_client:
        cl = ESPNClient(http_client=async_client)
        e2e_res = await cl.get_player_stats("football", "nfl", "401872947")
        rams_box = next(b for b in e2e_res["player_boxscores"] if b.get("team_id") == "14")
        assert rams_box["categories"][0]["athletes"][0]["position"] == "QB"
        await cl.close()

    # 7b. get_player_stats with boxscore: None
    def pstats_null_boxscore(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"boxscore": None})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(pstats_null_boxscore),
        base_url="https://site.api.espn.com",
    ) as async_client:
        cl_null = ESPNClient(http_client=async_client)
        null_res = await cl_null.get_player_stats("football", "nfl", "401872947")
        assert null_res["player_boxscores"] == []
        await cl_null.close()

    # 7c. get_player_stats when positions are already populated in boxscore
    def pstats_pos_already_present(request: httpx.Request) -> httpx.Response:
        if "roster" in str(request.url):
            pytest.fail("Roster should not be requested when positions are already present")
        return httpx.Response(
            200,
            json={
                "boxscore": {
                    "players": [
                        {
                            "team": {"id": "14", "displayName": "Rams"},
                            "statistics": [
                                {
                                    "athletes": [
                                        {
                                            "athlete": {
                                                "id": "12483",
                                                "displayName": "Matthew Stafford",
                                                "position": {"abbreviation": "QB"},
                                            },
                                            "stats": ["327"],
                                        }
                                    ]
                                }
                            ],
                        }
                    ]
                }
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(pstats_pos_already_present),
        base_url="https://site.api.espn.com",
    ) as async_client:
        cl_pres = ESPNClient(http_client=async_client)
        pres_res = await cl_pres.get_player_stats("football", "nfl", "401872947")
        assert pres_res["player_boxscores"][0]["categories"][0]["athletes"][0]["position"] == "QB"
        await cl_pres.close()

    await client.close()


def test_ref_normalization_and_payload_slimming() -> None:
    """Test ref normalization and payload slimming across groups, draft,
    scoreboard header, odds, play-by-play, predictor, and power index.
    """
    client = ESPNClient()

    # 1. _format_league_groups
    grp_not_list = client._format_league_groups({"groups": "not_a_list"}, "football", "nfl")
    assert grp_not_list["count"] == 1
    assert grp_not_list["groups"] == "not_a_list"

    grp_not_dict = client._format_league_groups(None, "football", "nfl")  # type: ignore[arg-type]
    assert grp_not_dict["groups"] == []

    grp_none = client._format_league_groups({"groups": None}, "football", "nfl")
    assert grp_none["count"] == 0
    assert grp_none["groups"] == []

    raw_grp = {
        "groups": [
            "non_dict_node",
            {
                "id": "conf1",
                "name": "NFC",
                "logos": [{"href": "logo.png"}],
                "links": [{"href": "link"}],
                "$ref": "http://.../groups/conf1",
                "teams": [
                    "raw_team_scalar",
                    {
                        "id": "14",
                        "name": "Rams",
                        "logos": [{"href": "logo.png"}],
                        "links": [{"href": "link"}],
                        "$ref": "http://.../teams/14",
                    },
                ],
                "children": [
                    {
                        "id": "div1",
                        "name": "NFC West",
                        "logos": [],
                    }
                ],
            },
        ]
    }
    fmt_grp = client._format_league_groups(raw_grp, "football", "nfl")
    assert fmt_grp["count"] == 2
    assert fmt_grp["groups"][0] == "non_dict_node"
    g1 = fmt_grp["groups"][1]
    assert "logos" not in g1 and "links" not in g1 and "$ref" not in g1
    assert g1["teams"][0] == "raw_team_scalar"
    assert "logos" not in g1["teams"][1] and "$ref" not in g1["teams"][1]
    assert "logos" not in g1["children"][0]

    # 2. _format_league_draft
    d_not_dict = client._format_league_draft("non_dict", "football", "nfl", 2026)  # type: ignore[arg-type]
    assert d_not_dict["draft"] == "non_dict"

    d_not_dict_content = client._format_league_draft(
        {"draft": "scalar_draft"}, "football", "nfl", 2026
    )
    assert d_not_dict_content["draft"] == "scalar_draft"

    raw_draft = {
        "draft": {
            "year": 2026,
            "broadcasts": [{"media": "ESPN"}],
            "links": [{"href": "url"}],
            "$ref": "http://.../draft/2026",
            "picks": [
                "invalid_pick_scalar",
                {
                    "overall": 1,
                    "links": [{"href": "pick_link"}],
                    "athlete": {
                        "id": "100",
                        "displayName": "Caleb Williams",
                        "position": {"name": "Quarterback"},
                        "team": {"id": "3"},
                        "attributes": [
                            {"name": "Height", "displayValue": "6-1"},
                            "invalid_attribute_scalar",
                        ],
                    },
                },
                {
                    "overall": 2,
                    "athlete": {
                        "id": "101",
                        "displayName": "Jayden Daniels",
                        "position": "QB",
                        "attributes": None,
                    },
                },
                {
                    "overall": 3,
                },
            ],
        }
    }
    fmt_draft = client._format_league_draft(raw_draft, "football", "nfl", 2026)
    clean_d = fmt_draft["draft"]
    assert "broadcasts" not in clean_d and "links" not in clean_d and "$ref" not in clean_d
    assert len(clean_d["picks"]) == 3
    assert clean_d["picks"][0]["athlete"]["position"] == "Quarterback"
    assert clean_d["picks"][0]["athlete"]["attributes"] == [{"name": "Height", "value": "6-1"}]
    assert "links" not in clean_d["picks"][0]
    assert clean_d["picks"][1]["athlete"]["position"] == "QB"

    raw_picks_only = {
        "picks": [
            {
                "overall": 4,
                "athlete": {
                    "id": "102",
                    "displayName": "Marvin Harrison Jr.",
                    "position": "WR",
                    "attributes": [{"name": "Weight", "value": "205"}],
                },
            }
        ]
    }
    fmt_picks_only = client._format_league_draft(raw_picks_only, "football", "nfl", 2026)
    assert len(fmt_picks_only["draft"]) == 1
    assert fmt_picks_only["draft"][0]["athlete"]["attributes"] == [
        {"name": "Weight", "value": "205"}
    ]

    # 3. _format_scoreboard_header
    hdr_fallback_leagues = client._format_scoreboard_header(
        {"leagues": [{"id": "nfl"}]}, "football", "nfl"
    )
    assert hdr_fallback_leagues["sports"] == [{"id": "nfl"}]

    hdr_fallback_scalar = client._format_scoreboard_header("raw_string", "football", "nfl")  # type: ignore[arg-type]
    assert hdr_fallback_scalar["sports"] == "raw_string"

    raw_hdr = {
        "sports": [
            "invalid_sport",
            {
                "id": "1",
                "name": "football",
                "slug": "football",
                "leagues": [
                    "invalid_league",
                    {
                        "id": "28",
                        "name": "National Football League",
                        "abbreviation": "NFL",
                        "slug": "nfl",
                        "events": [
                            "invalid_event",
                            {
                                "id": "401872947",
                                "name": "Rams at Seahawks",
                                "shortName": "LAR @ SEA",
                                "date": "2026-09-25T20:00Z",
                                "summary": "Final",
                                "period": 4,
                                "clock": "0:00",
                                "status": {"type": {"completed": True}},
                                "competitors": [
                                    "invalid_competitor",
                                    {
                                        "id": "14",
                                        "homeAway": "away",
                                        "score": "24",
                                        "winner": True,
                                        "team": {
                                            "id": "14",
                                            "name": "Rams",
                                            "displayName": "Los Angeles Rams",
                                            "abbreviation": "LAR",
                                        },
                                    },
                                    {
                                        "id": "26",
                                        "homeAway": "home",
                                        "score": "20",
                                        "winner": False,
                                    },
                                ],
                            },
                        ],
                    },
                ],
            },
        ]
    }
    fmt_hdr = client._format_scoreboard_header(raw_hdr, "football", "nfl")
    assert len(fmt_hdr["sports"]) == 1
    lg_out = fmt_hdr["sports"][0]["leagues"][0]
    assert len(lg_out["events"]) == 1
    ev_out = lg_out["events"][0]
    assert ev_out["id"] == "401872947"
    assert len(ev_out["competitors"]) == 2
    assert ev_out["competitors"][0]["team"]["abbreviation"] == "LAR"
    assert "team" not in ev_out["competitors"][1]

    # 4. _format_event_odds
    odds_non_dict_item = client._format_event_odds(
        {"items": ["non_dict_odd"]}, "football", "nfl", "1", "1"
    )
    assert odds_non_dict_item["odds"] == []

    raw_single_odd = {
        "$ref": "http://sports.core.api.espn.com/v2/sports/football/leagues/nfl/events/1/competitions/1/odds/1001",
        "links": [{"href": "url"}],
        "provider": {
            "$ref": "http://sports.core.api.espn.com/v2/sports/football/leagues/nfl/providers/38",
            "name": "DraftKings",
        },
        "homeTeamOdds": {
            "team": {
                "$ref": "http://sports.core.api.espn.com/v2/sports/football/leagues/nfl/teams/14?lang=en"
            },
            "moneyLine": -110,
        },
        "awayTeamOdds": {
            "team": {
                "$ref": "http://sports.core.api.espn.com/v2/sports/football/leagues/nfl/teams/26?lang=en"
            },
            "moneyLine": 100,
        },
        "propBets": {"items": []},
    }
    fmt_single_odd = client._format_event_odds(raw_single_odd, "football", "nfl", "1", "1")
    assert len(fmt_single_odd["odds"]) == 1
    odd_0 = fmt_single_odd["odds"][0]
    assert odd_0["id"] == "1001"
    assert odd_0["provider"]["id"] == "38"
    assert odd_0["homeTeamOdds"]["team_id"] == "14"
    assert odd_0["homeTeamOdds"]["team"] == {"id": "14"}
    assert odd_0["awayTeamOdds"]["team_id"] == "26"
    assert "propBets" not in odd_0
    assert "$ref" not in odd_0 and "links" not in odd_0

    raw_odd_no_ids = {
        "items": [
            {
                "provider": {"name": "NoRef"},
                "homeTeamOdds": {"team": {"name": "NoRef"}},
            }
        ]
    }
    fmt_odd_no_ids = client._format_event_odds(raw_odd_no_ids, "football", "nfl", "1", "1")
    assert "team_id" not in fmt_odd_no_ids["odds"][0]["homeTeamOdds"]
    assert "id" not in fmt_odd_no_ids["odds"][0]["provider"]

    fmt_empty_odd = client._format_event_odds({}, "football", "nfl", "1", "1")
    assert fmt_empty_odd["odds"] == {}

    raw_odd_list_prop = {
        "items": [
            {
                "provider": {"name": "NoRef"},
                "propBets": ["prop1", "prop2"],
            }
        ]
    }
    fmt_odd_list_prop = client._format_event_odds(raw_odd_list_prop, "football", "nfl", "1", "1")
    assert "propBets" not in fmt_odd_list_prop["odds"][0]

    # 5. _format_play_by_play
    pbp_non_dict = client._format_play_by_play(
        {"items": ["non_dict_play"]}, "football", "nfl", "1", "1"
    )
    assert pbp_non_dict["count"] == 0
    assert pbp_non_dict["plays"] == []

    raw_pbp = {
        "items": [
            {
                "id": "play1",
                "team": {
                    "$ref": "http://sports.core.api.espn.com/v2/sports/football/leagues/nfl/teams/14"
                },
                "period": {
                    "$ref": "http://sports.core.api.espn.com/v2/sports/football/leagues/nfl/seasons/2026/types/2/weeks/1/events/1/periods/2",
                    "number": 2,
                },
                "participants": [
                    "invalid_participant",
                    {
                        "athlete": {
                            "$ref": "http://sports.core.api.espn.com/v2/sports/football/leagues/nfl/athletes/12483"
                        },
                        "position": {
                            "$ref": "http://sports.core.api.espn.com/v2/sports/football/leagues/nfl/positions/8"
                        },
                        "statistics": [{"name": "passingYards"}],
                    },
                    {
                        "athlete": {"name": "NoRef"},
                        "position": {"name": "NoRef"},
                    },
                ],
                "teamParticipants": [
                    "invalid_tpart",
                    {
                        "team": {
                            "$ref": "http://sports.core.api.espn.com/v2/sports/football/leagues/nfl/teams/14"
                        },
                        "statistics": [{"name": "firstDowns"}],
                    },
                    {
                        "team": {"name": "NoRef"},
                    },
                ],
            },
            {
                "id": "play2",
                "period": {
                    "$ref": "http://sports.core.api.espn.com/v2/sports/football/leagues/nfl/events/1/periods/3"
                },
            },
            {
                "id": "play3",
                "period": {},
            },
            {
                "id": "play4",
                "period": {"number": 0},
            },
            {
                "id": "play5",
                "period": {
                    "$ref": "http://sports.core.api.espn.com/v2/sports/football/leagues/nfl/events/1/periods/ot"
                },
            },
        ]
    }
    fmt_pbp = client._format_play_by_play(raw_pbp, "football", "nfl", "1", "1")
    assert fmt_pbp["count"] == 5
    p0 = fmt_pbp["plays"][0]
    assert p0["team_id"] == "14"
    assert p0["team"] == {"id": "14"}
    assert p0["period"] == {"number": 2}
    assert p0["participants"][0]["athlete_id"] == "12483"
    assert p0["participants"][0]["athlete"] == {"id": "12483"}
    assert p0["participants"][0]["position_id"] == "8"
    assert "statistics" not in p0["participants"][0]
    assert "athlete_id" not in p0["participants"][1]
    assert p0["teamParticipants"][0]["team_id"] == "14"
    assert p0["teamParticipants"][0]["team"] == {"id": "14"}
    assert "statistics" not in p0["teamParticipants"][0]
    assert "team_id" not in p0["teamParticipants"][1]
    p1 = fmt_pbp["plays"][1]
    assert p1["period"] == {"number": 3}
    p2 = fmt_pbp["plays"][2]
    assert p2["period"] == {}
    p3 = fmt_pbp["plays"][3]
    assert p3["period"] == {"number": 0}
    p4 = fmt_pbp["plays"][4]
    assert p4["period"] == {"number": "ot"}

    pbp_scalar = client._format_play_by_play("scalar_pbp", "football", "nfl", "1", "1")  # type: ignore[arg-type]
    assert pbp_scalar["count"] == 0
    assert pbp_scalar["plays"] == []

    pbp_single = client._format_play_by_play({"id": "play_single"}, "football", "nfl", "1", "1")
    assert pbp_single["count"] == 1
    assert pbp_single["plays"][0]["id"] == "play_single"

    # 6. _format_game_predictor
    pred_scalar = client._format_game_predictor("scalar_pred", "football", "nfl", "1", "1")  # type: ignore[arg-type]
    assert pred_scalar["predictor"] == "scalar_pred"

    raw_pred = {
        "$ref": "http://sports.core.api.espn.com/v2/sports/football/leagues/nfl/events/1/competitions/1/predictor",
        "links": [{"href": "url"}],
        "homeTeam": {
            "chanceLoss": 45.0,
            "team": {
                "$ref": "http://sports.core.api.espn.com/v2/sports/football/leagues/nfl/teams/14"
            },
            "links": [{"href": "url"}],
        },
        "awayTeam": {
            "chanceLoss": 55.0,
            "team": {
                "$ref": "http://sports.core.api.espn.com/v2/sports/football/leagues/nfl/teams/26"
            },
        },
    }
    fmt_pred = client._format_game_predictor(raw_pred, "football", "nfl", "1", "1")
    p_dict = fmt_pred["predictor"]
    assert "$ref" not in p_dict and "links" not in p_dict
    assert p_dict["homeTeam"]["team_id"] == "14"
    assert p_dict["homeTeam"]["team"] == {"id": "14"}
    assert "links" not in p_dict["homeTeam"]
    assert p_dict["awayTeam"]["team_id"] == "26"

    raw_pred_no_ref = {
        "homeTeam": {"team": {"name": "Rams"}},
        "awayTeam": {"team": {"name": "Seahawks"}},
    }
    fmt_pred_no_ref = client._format_game_predictor(raw_pred_no_ref, "football", "nfl", "1", "1")
    assert "team_id" not in fmt_pred_no_ref["predictor"]["homeTeam"]

    # 7. _format_power_index: links, logos, $ref stripped
    raw_fpi = {
        "items": [
            {
                "rank": 1,
                "links": [{"href": "fpi_url"}],
                "logos": [{"href": "logo_url"}],
                "$ref": "http://sports.core.api.espn.com/v2/sports/football/leagues/nfl/seasons/2026/fpi/14",
                "team": {
                    "$ref": "http://sports.core.api.espn.com/v2/sports/football/leagues/nfl/teams/14",
                    "displayName": "Los Angeles Rams",
                    "abbreviation": "LAR",
                },
            }
        ]
    }
    fmt_fpi = client._format_power_index(raw_fpi, "football", "nfl", 2026)
    fpi_0 = fmt_fpi["power_index"][0]
    assert "links" not in fpi_0 and "logos" not in fpi_0 and "$ref" not in fpi_0
    assert fpi_0["team"]["id"] == "14"
    assert fpi_0["team"]["name"] == "Los Angeles Rams"
