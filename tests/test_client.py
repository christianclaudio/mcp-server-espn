"""Tests for async ESPN HTTP client functionality, alias normalization, and domain methods."""

import socket
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

    # Valid public resolution passes to base transport
    with patch(
        "socket.getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
    ):
        with patch.object(
            httpx.AsyncHTTPTransport,
            "handle_async_request",
            return_value=httpx.Response(200, json={"ok": True}),
        ):
            req = httpx.Request("GET", "https://api.customdomain.org/data")
            resp = await transport.handle_async_request(req)
            assert resp.status_code == 200

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

    # 17. Futures
    fut = await client.get_futures("football", "nfl", season=2026)
    assert fut["season"] == 2026
    assert len(fut["futures"]) == 1

    # 18. Power index
    fpi = await client.get_power_index("football", "nfl", season=2026)
    assert fpi["season"] == 2026
    assert fpi["power_index"][0]["rank"] == 4

    await client.close()
