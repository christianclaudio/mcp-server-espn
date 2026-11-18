"""Offline coverage for the core routes added from the live WADL check."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastmcp.exceptions import ToolError

import espn_mcp.server as server
import espn_mcp.tools.games as games
import espn_mcp.tools.teams as teams
from espn_mcp.client import ESPNClient
from espn_mcp.server import create_server

FIXTURES = Path(__file__).parent / "fixtures" / "coverage"
CORE = "sports.core.api.espn.com"

NEW_TOOLS = (
    "games_get_league_injuries",
    "games_get_drives",
    "games_get_drive_plays",
    "games_get_qbr",
    "games_list_golf_tournaments",
    "games_get_golf_tournament",
    "games_get_bets",
    "games_get_bet_type",
    "games_get_awards",
    "games_get_award",
    "teams_list_coaches",
    "teams_get_coach",
    "teams_list_venues",
    "teams_get_venue",
    "teams_list_franchises",
    "teams_get_franchise",
    "teams_get_team_tournament_history",
    "teams_list_recruits",
    "teams_get_recruiting_rankings",
)


def _body(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _qs(request: httpx.Request) -> dict[str, str]:
    query = urlsplit(str(request.url)).query
    parsed = parse_qs(query)
    return {key: values[-1] for key, values in parsed.items()}


class _Router:
    def __init__(self, routes: list[tuple[str, int, dict[str, Any]]]) -> None:
        self.routes = routes
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        path = request.url.path
        for needle, status, body in self.routes:
            if path == needle or path.endswith(needle):
                return httpx.Response(status, json=body)
        return httpx.Response(404, json=_body("not_found_error.json"))


@pytest.fixture
def bind(monkeypatch: pytest.MonkeyPatch):
    async def _bind(routes: list[tuple[str, int, dict[str, Any]]]) -> _Router:
        router = _Router(routes)
        http = httpx.AsyncClient(transport=httpx.MockTransport(router))
        monkeypatch.setattr(server, "client", ESPNClient(http_client=http, max_retries=0))
        return router

    return _bind


@pytest.mark.asyncio
async def test_new_tools_are_unofficial_and_read_only() -> None:
    tools = {tool.name: tool for tool in await create_server(profile="full").list_tools()}
    for name in NEW_TOOLS:
        description = tools[name].description or ""
        assert description.startswith("Unofficial: ESPN publishes no docs for this route.")
        assert tools[name].annotations is not None
        assert tools[name].annotations.read_only_hint is True
        assert tools[name].annotations.destructive_hint is False
    for name in ("games_get_qbr", "games_get_drives", "games_get_drive_plays"):
        assert "NFL and college football only" in (tools[name].description or "")
    for name in ("games_list_golf_tournaments", "games_get_golf_tournament"):
        assert "Golf only" in (tools[name].description or "")
    for name in ("teams_list_recruits", "teams_get_recruiting_rankings"):
        assert "College only" in (tools[name].description or "")
    assert "Tournament leagues only" in (
        tools["teams_get_team_tournament_history"].description or ""
    )
    bets = tools["games_get_bets"].description or ""
    assert "betting content; US availability varies" in bets
    assert "Can return no items outside betting windows" in bets


@pytest.mark.asyncio
async def test_league_injuries_page(bind) -> None:
    router = await bind([("/v3/sports/football/nfl/injuries", 200, _body("injuries.json"))])
    result = await games.get_league_injuries(sport="football", league="nfl", page=2, limit=1)
    assert result["status"] == "success"
    data = result["data"]
    assert data["items"][0]["status"] == "Questionable"
    assert data["items"][0]["id"] == "-2080274"
    assert data["count"] == 1040
    request = router.seen[0]
    assert request.url.host == CORE
    assert request.url.path == "/v3/sports/football/nfl/injuries"
    assert _qs(request) == {"page": "2", "limit": "1"}


@pytest.mark.asyncio
async def test_injuries_without_items_are_an_empty_page(bind) -> None:
    await bind([("/injuries", 200, {"count": 0})])
    result = await games.get_league_injuries(sport="nfl", league="nfl")
    assert result["data"]["items"] == []
    assert result["data"]["count"] == 0


@pytest.mark.asyncio
async def test_drives_default_competition_and_hostile_event_id(bind) -> None:
    router = await bind([("/drives", 200, _body("drives.json"))])
    result = await games.get_drives(sport="football", league="nfl", event_id="401873058")
    assert result["data"]["items"][0]["id"] == "4018730581"
    assert result["data"]["competition_id"] == "401873058"
    assert router.seen[0].url.path.endswith(
        "/v3/sports/football/nfl/events/401873058/competitions/401873058/drives"
    )
    await games.get_drives(sport="football", league="nfl", event_id="../401873058")
    raw = router.seen[1].url.raw_path.decode()
    assert ".." not in raw
    assert "%2E%2E" in raw


@pytest.mark.asyncio
async def test_drive_plays(bind) -> None:
    router = await bind([("/plays", 200, _body("drive_plays.json"))])
    result = await games.get_drive_plays(
        sport="football",
        league="nfl",
        event_id="401873058",
        drive_id="4018730581",
        competition_id="401873058",
    )
    play = result["data"]["items"][0]
    assert play["text"].startswith("H.Butker kicks")
    assert play["team"]["id"] == "1"
    assert router.seen[0].url.path.endswith("/drives/4018730581/plays")
    assert router.seen[0].url.host == CORE


@pytest.mark.asyncio
async def test_qbr_league_season_and_week(bind) -> None:
    router = await bind(
        [
            ("/weeks/10/qbr/0", 200, _body("qbr_week.json")),
            ("/seasons/2026/qbr", 200, _body("qbr.json")),
            ("/qbr", 200, _body("qbr.json")),
        ]
    )
    league = await games.get_qbr(sport="football", league="nfl")
    assert league["data"]["scope"] == "league"
    assert league["data"]["items"][0]["statistics"]["qbr"]["qbr"]["value"] == 88.49
    assert router.seen[0].url.path == "/v3/sports/football/nfl/qbr"

    season = await games.get_qbr(sport="football", league="nfl", season=2026)
    assert season["data"]["scope"] == "season"
    assert router.seen[1].url.path == "/v3/sports/football/nfl/seasons/2026/qbr"

    week = await games.get_qbr(sport="football", league="nfl", season=2026, week=10)
    assert week["data"]["scope"] == "week"
    assert week["data"]["items"][0]["athlete"]["id"] == "8439"
    assert week["data"]["items"][0]["team"]["id"] == "23"
    assert router.seen[2].url.path.endswith("/seasons/2026/types/2/weeks/10/qbr/0")


@pytest.mark.asyncio
async def test_qbr_week_resolves_the_current_season(bind) -> None:
    router = await bind(
        [
            ("/leagues/nfl", 200, _body("league.json")),
            ("/qbr/0", 200, _body("qbr_week.json")),
        ]
    )
    result = await games.get_qbr(sport="football", league="nfl", week=10)
    assert result["data"]["season"] == 2026
    assert router.seen[0].url.path == "/v2/sports/football/leagues/nfl"
    assert "/seasons/2026/types/2/weeks/10/qbr/0" in router.seen[1].url.path


@pytest.mark.asyncio
async def test_golf_tournaments_are_golf_only(bind) -> None:
    router = await bind(
        [
            ("/tournaments/1", 200, _body("golf_tournament.json")),
            ("/tournaments", 200, _body("golf_tournaments.json")),
        ]
    )
    listed = await games.list_golf_tournaments(league="pga")
    assert listed["data"]["items"][0]["displayName"] == "Hyundai Tournament of Champions"
    assert router.seen[0].url.path == "/v3/sports/golf/pga/tournaments"
    one = await games.get_golf_tournament(league="lpga", tournament_id="1")
    assert one["data"]["displayName"] == "Hyundai Tournament of Champions"
    assert router.seen[1].url.path == "/v2/sports/golf/leagues/lpga/tournaments/1"
    with pytest.raises(ToolError, match="Golf tournaments are golf only"):
        await games.list_golf_tournaments(league="nfl")


@pytest.mark.asyncio
async def test_bets_trim_and_empty_featured(bind) -> None:
    router = await bind(
        [
            ("/bets/featured", 200, _body("bets_featured.json")),
            ("/bets/trending", 200, _body("bets_trending.json")),
        ]
    )
    featured = await games.get_bets(kind="featured", limit=5)
    assert featured["data"]["count"] == 0
    assert featured["data"]["items"] == []
    trending = await games.get_bets(kind="trending", limit=1)
    assert trending["data"]["count"] == 1167
    assert len(trending["data"]["items"]) == 1
    assert trending["data"]["items"][0]["name"] == "Panthers @ 49ers"
    assert trending["data"]["items"][0]["odds"]["american"] == "+2364"
    assert _qs(router.seen[1])["limit"] == "1"
    with pytest.raises(ToolError, match="kind must be 'featured' or 'trending'"):
        await games.get_bets(kind="parlay")


@pytest.mark.asyncio
async def test_bet_type(bind) -> None:
    router = await bind([("/bet-types/1", 200, _body("bet_type.json"))])
    result = await games.get_bet_type(sport="football", league="nfl", bet_type_id="1")
    assert result["data"]["name"] == "spread"
    assert result["data"]["id"] == 1
    assert router.seen[0].url.path.endswith("/bet-types/1")


@pytest.mark.asyncio
async def test_awards_refs_and_default_season(bind) -> None:
    router = await bind(
        [
            ("/leagues/nfl", 200, _body("league.json")),
            ("/awards/317", 200, _body("award.json")),
            ("/awards", 200, _body("awards.json")),
        ]
    )
    listed = await games.get_awards(sport="football", league="nfl", season=2025)
    assert [item["id"] for item in listed["data"]["items"]] == ["317", "477"]
    assert listed["data"]["items"][0]["ref"].endswith("/awards/317?lang=en&region=us")
    assert router.seen[0].url.path.endswith("/seasons/2025/awards")
    one = await games.get_award(sport="football", league="nfl", season=2025, award_id="317")
    assert one["data"]["name"] == "Super Bowl MVP"
    assert one["data"]["winners"][0]["athlete"]["id"] == "4567048"
    assert one["data"]["winners"][0]["note"] == "27 carries, 135 yards"
    default = await games.get_awards(sport="football", league="nfl")
    assert default["data"]["season"] == 2026
    assert router.seen[2].url.path == "/v2/sports/football/leagues/nfl"


@pytest.mark.asyncio
async def test_coaches(bind) -> None:
    router = await bind(
        [
            ("/coaches/17553", 200, _body("coach.json")),
            ("/seasons/2026/coaches", 200, _body("coaches.json")),
            ("/coaches", 200, _body("coaches.json")),
        ]
    )
    listed = await teams.list_coaches(sport="football", league="nfl")
    assert listed["data"]["items"][0]["fullName"] == "Mike LaFleur"
    assert listed["data"]["season"] is None
    assert router.seen[0].url.path == "/v3/sports/football/nfl/coaches"
    season = await teams.list_coaches(sport="football", league="nfl", season=2026)
    assert router.seen[1].url.path == "/v3/sports/football/nfl/seasons/2026/coaches"
    assert season["data"]["season"] == 2026
    coach = await teams.get_coach(sport="football", league="nfl", coach_id="17553", season=2026)
    assert coach["data"]["lastName"] == "Reid"
    assert coach["data"]["team"]["id"] == "12"
    assert coach["data"]["experience"] == 27
    assert router.seen[2].url.path.endswith("/seasons/2026/coaches/17553")


@pytest.mark.asyncio
async def test_venues_skip_non_refs_and_hostile_id(bind) -> None:
    page = _body("venues.json")
    page["items"] = ["skip-me", {"name": "no-ref"}, page["items"][0]]
    router = await bind(
        [
            ("/venues/16", 200, _body("venue.json")),
            ("/venues", 200, page),
        ]
    )
    listed = await teams.list_venues(sport="football", league="nfl", page=1, limit=2)
    assert listed["data"]["items"] == [
        {
            "id": "16",
            "ref": "http://sports.core.api.espn.com/v2/sports/football/leagues/nfl/venues/16?lang=en&region=us",
        }
    ]
    assert _qs(router.seen[0]) == {"page": "1", "limit": "2"}
    one = await teams.get_venue(sport="football", league="nfl", venue_id="16")
    assert one["data"]["fullName"] == "Mall of America Field"
    assert one["data"]["indoor"] is True
    with pytest.raises(ToolError):
        await teams.get_venue(sport="football", league="nfl", venue_id="../16")
    raw = router.seen[2].url.raw_path.decode()
    assert ".." not in raw
    assert "%2E%2E" in raw
    await bind([("/venues", 200, {"count": 0})])
    empty = await teams.list_venues(sport="football", league="nfl")
    assert empty["data"]["items"] == []


@pytest.mark.asyncio
async def test_franchises(bind) -> None:
    router = await bind(
        [
            ("/franchises/12", 200, _body("franchise.json")),
            ("/franchises", 200, _body("franchises.json")),
        ]
    )
    listed = await teams.list_franchises(sport="football", league="nfl")
    assert [item["id"] for item in listed["data"]["items"]] == ["1", "2"]
    one = await teams.get_franchise(sport="football", league="nfl", franchise_id="12")
    assert one["data"]["displayName"] == "Kansas City Chiefs"
    assert one["data"]["venue"]["fullName"] == "Arrowhead Stadium"
    assert router.seen[1].url.path.endswith("/franchises/12")


@pytest.mark.asyncio
async def test_tournament_history_and_nfl_rejection(bind) -> None:
    router = await bind(
        [
            (
                "/football/leagues/nfl/teams/12/history/1",
                400,
                _body("history_nfl_error.json"),
            ),
            ("/history/1", 200, _body("history.json")),
        ]
    )
    result = await teams.get_team_tournament_history(
        sport="basketball",
        league="mens-college-basketball",
        team_id="150",
        history_type="1",
    )
    assert result["data"]["displayName"] == "NCAA Tournament History"
    assert result["data"]["type"] == "tournament-ncaa"
    assert result["data"]["statistics"][0]["displayValue"] == "126"
    assert result["data"]["championships"][0]["tournament"]["id"] == "22"
    assert router.seen[0].url.path.endswith("/teams/150/history/1")
    with pytest.raises(ToolError, match="not supported for football/nfl"):
        await teams.get_team_tournament_history(sport="football", league="nfl", team_id="12")


@pytest.mark.asyncio
async def test_recruits_and_rankings(bind) -> None:
    router = await bind(
        [
            ("/rankings", 200, _body("rankings.json")),
            ("/recruits", 200, _body("recruits.json")),
        ]
    )
    recruits = await teams.list_recruits(
        sport="football", league="college-football", year=2027, page=1, limit=1
    )
    assert recruits["data"]["items"][0]["id"] == "272275"
    assert recruits["data"]["year"] == 2027
    assert router.seen[0].url.path.endswith("/seasons/2027/recruits")
    rankings = await teams.get_recruiting_rankings(
        sport="football", league="college-football", year=2027
    )
    assert rankings["data"]["items"][0]["id"] == "1"
    assert router.seen[1].url.path.endswith("/recruiting/2027/rankings")


@pytest.mark.asyncio
async def test_missing_venue_uses_the_live_not_found_message(bind) -> None:
    await bind([])
    with pytest.raises(ToolError, match="no instance found"):
        await teams.get_venue(sport="football", league="nfl", venue_id="999999")
