"""Tests for async ESPN HTTP client functionality, alias normalization, and domain methods."""

import asyncio
import socket
import threading
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
                            "id": "501",
                            "name": "Rams at Broncos",
                            "date": "2026-09-28",
                            "competitions": [
                                {
                                    "status": {
                                        "type": {
                                            "state": "pre",
                                            "detail": "Scheduled",
                                            "completed": False,
                                        }
                                    },
                                    "competitors": [
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
                200, json={"team": {"id": "14", "displayName": "Rams", "nextEvent": []}}
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
