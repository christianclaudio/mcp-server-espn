"""Tool Search backends, parameter docs, standings season echo, postseason schedules, 404 hint.

Each behavior here came from a failed task in the 2026-10-17 tool-use eval: plain-language
searches that found nothing on the regex backend, a standings loop on the wrong season, and a
guessed World Series event ID that got a bare 404.
"""

from __future__ import annotations

import ast
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.transforms.search import BM25SearchTransform, RegexSearchTransform

import espn_mcp
from espn_mcp import server
from espn_mcp.client import ESPNClient
from espn_mcp.config import settings
from espn_mcp.errors import ESPNNotFoundError
from espn_mcp.server import create_server, main
from espn_mcp.tools.params import SEASON_CONVENTION, SEASON_SHORT

TOOL_NAME = re.compile(r"(?:games|teams|news)_[a-z_]+")


def _found(result: Any) -> list[str]:
    names: list[str] = []
    for name in TOOL_NAME.findall(str(result.content)):
        if name not in names:
            names.append(name)
    return names


def _search_transforms(app: FastMCP) -> list[type]:
    return [
        type(t)
        for t in app.transforms
        if isinstance(t, (RegexSearchTransform, BM25SearchTransform))
    ]


# ── Tool Search backend ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tool_search_defaults_to_regex() -> None:
    app = create_server(profile="full", enable_tool_search=True)
    assert settings.MCP_TOOL_SEARCH_BACKEND == "regex"
    assert _search_transforms(app) == [RegexSearchTransform]
    assert [t.name for t in await app.list_tools()] == ["search_tools", "call_tool"]


@pytest.mark.asyncio
async def test_tool_search_setting_selects_bm25(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "MCP_TOOL_SEARCH_BACKEND", "bm25")
    app = create_server(profile="full", enable_tool_search=True)
    assert _search_transforms(app) == [BM25SearchTransform]


@pytest.mark.asyncio
async def test_bm25_tool_search_ranks_natural_language_queries() -> None:
    """bm25 takes a plain-language query and ranks the right tool first."""
    app = create_server(profile="full", enable_tool_search=True, tool_search_backend="bm25")
    assert _search_transforms(app) == [BM25SearchTransform]
    assert [t.name for t in await app.list_tools()] == ["search_tools", "call_tool"]

    standings = await app.call_tool("search_tools", {"query": "NBA best record standings"})
    assert not standings.is_error
    assert _found(standings)[0] == "games_get_standings"

    draft = await app.call_tool("search_tools", {"query": "NFL draft first overall pick"})
    assert _found(draft)[0] == "games_get_league_draft"

    final = await app.call_tool("search_tools", {"query": "Super Bowl final score"})
    assert _found(final)[0] == "games_get_scoreboard"


@pytest.mark.asyncio
async def test_regex_tool_search_finds_domain_keywords() -> None:
    """Keywords the eval model searched for now appear in tool text."""
    app = create_server(profile="full", enable_tool_search=True, tool_search_backend="regex")
    world_series = await app.call_tool("search_tools", {"pattern": "World Series"})
    assert _found(world_series)[0] == "games_get_scoreboard"
    baseball = await app.call_tool("search_tools", {"pattern": "baseball"})
    assert _found(baseball) == ["games_get_scoreboard"]
    athlete_id = await app.call_tool("search_tools", {"pattern": "athlete ID"})
    assert _found(athlete_id) == ["teams_search"]
    best_record = await app.call_tool("search_tools", {"pattern": "best record"})
    assert _found(best_record) == ["games_get_standings"]


def test_main_cli_tool_search_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """--tool-search-backend bm25 reaches create_server."""
    captured: dict[str, Any] = {}
    real_create = server.create_server

    def _create(**kwargs: Any) -> FastMCP:
        captured.update(kwargs)
        app = real_create(**kwargs)
        monkeypatch.setattr(app, "run", MagicMock())
        return app

    monkeypatch.setattr(server, "create_server", _create)
    monkeypatch.setattr(
        "sys.argv", ["espn-mcp", "--enable-tool-search", "--tool-search-backend", "bm25"]
    )
    main()
    assert captured["enable_tool_search"] is True
    assert captured["tool_search_backend"] == "bm25"


def test_main_cli_rejects_unknown_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["espn-mcp", "--tool-search-backend", "fuzzy"])
    with pytest.raises(SystemExit):
        main()


# ── Parameter and tool descriptions ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sport_league_season_params_are_documented() -> None:
    tools = {t.name: t for t in await create_server(profile="full").list_tools()}
    for tool in tools.values():
        props = tool.parameters.get("properties", {})
        for param in ("sport", "league"):
            if param in props:
                assert props[param].get("description"), f"{tool.name}.{param}"
    standings = tools["games_get_standings"].parameters["properties"]["season"]["description"]
    assert standings.startswith(SEASON_CONVENTION)
    assert "2015-16" in standings
    assert "soccer: the year it starts" in standings
    # Season descriptions stand on their own: a model may see only the tools it was given
    # or found, so none of them names another tool.
    for tool in tools.values():
        season_param = tool.parameters.get("properties", {}).get("season")
        if season_param:
            assert not TOOL_NAME.search(season_param["description"]), tool.name
    for name in (
        "games_get_team_schedule",
        "teams_get_athlete_gamelog",
        "teams_get_athlete_splits",
        "games_get_futures",
        "games_get_power_index",
    ):
        desc = tools[name].parameters["properties"]["season"]["description"]
        assert SEASON_SHORT in desc, name
    for name in ("games_get_futures", "games_get_power_index"):
        desc = tools[name].parameters["properties"]["season"]["description"]
        assert "Omit for ESPN's current." in desc
        assert "calendar year" not in desc
    stats_season = tools["teams_get_athlete_stats"].parameters["properties"]["season"]
    assert "every season's totals" in stats_season["description"]
    draft_season = tools["games_get_league_draft"].parameters["properties"]["season"]
    assert draft_season["description"].startswith("Draft year")
    for name in (
        "games_get_league_draft",
        "games_get_futures",
        "games_get_power_index",
        "teams_get_athlete_stats",
        "teams_get_athlete_gamelog",
        "teams_get_athlete_splits",
    ):
        assert "season" not in tools[name].parameters.get("required", []), name
    schedule = tools["games_get_team_schedule"]
    assert "season_type" in schedule.parameters["properties"]
    assert "season_type" not in schedule.parameters.get("required", [])
    assert "postseason" in (schedule.description or "")
    assert (tools["games_get_scoreboard"].description or "").startswith(
        "Fetch final scores and results for any date"
    )
    assert "athlete ID" in (tools["teams_search"].description or "")
    assert "season totals" in (tools["teams_get_athlete_stats"].description or "")


# ── Standings season echo (additive) ────────────────────────────────────────────

_STANDINGS_2016 = {
    "name": "National Basketball Association",
    "children": [
        {
            "name": "Western Conference",
            "standings": {
                "season": 2016,
                "seasonType": 2,
                "seasonDisplayName": "2015-16",
                "entries": [
                    {
                        "team": {"id": "9", "displayName": "Golden State Warriors"},
                        "stats": [{"name": "wins", "displayValue": "73"}],
                    }
                ],
            },
        },
        {"name": "Eastern Conference", "standings": {"season": 2016, "entries": []}},
    ],
}


@pytest.mark.asyncio
async def test_standings_echo_requested_and_returned_season() -> None:
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200, json=_STANDINGS_2016)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://site.web.api.espn.com"
    ) as hc:
        out = await ESPNClient(http_client=hc).get_standings("basketball", "nba", season=2016)

    assert seen[0].params["season"] == "2016"
    assert out["requested_season"] == 2016
    assert out["season"] == 2016
    assert out["season_display"] == "2015-16"
    # Existing fields are unchanged.
    assert out["sport"] == "basketball"
    assert out["league"] == "nba"
    assert out["count"] == 1
    assert out["standings"][0]["name"] == "Golden State Warriors"


def test_standings_season_fields_are_none_without_labels() -> None:
    out = ESPNClient()._format_standings({"standings": {"entries": []}}, "baseball", "mlb")
    assert out["requested_season"] is None
    assert out["season"] is None
    assert out["season_display"] is None
    assert out["count"] == 0


# ── Team schedule season_type (additive) ────────────────────────────────────────


@pytest.mark.asyncio
async def test_team_schedule_season_type_sent_only_when_set() -> None:
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200, json={"requestedSeason": {"year": 2016}, "events": []})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://site.web.api.espn.com"
    ) as hc:
        client = ESPNClient(http_client=hc)
        post = await client.get_team_schedule("baseball", "mlb", "16", season=2016, season_type=3)
        default = await client.get_team_schedule("baseball", "mlb", "16", season=2016)

    assert seen[0].params["seasontype"] == "3"
    assert seen[0].params["season"] == "2016"
    assert "seasontype" not in seen[1].params
    assert post["season"] == default["season"] == 2016


@pytest.mark.asyncio
async def test_team_schedule_tool_passes_season_type(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200, json={"events": []})

    hc = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://site.web.api.espn.com"
    )
    monkeypatch.setattr(server, "client", ESPNClient(http_client=hc))
    res = await server.get_team_schedule(
        sport="baseball", league="mlb", team_id="16", season=2016, season_type=3
    )
    await hc.aclose()
    assert res["status"] == "success"
    assert seen[0].params["seasontype"] == "3"


# ── 404 hint for an unknown event ID ────────────────────────────────────────────


_EVENT_ID_HINT = (
    "Get valid event IDs from the scoreboard for that date, or the team's schedule "
    "(postseason: season_type=3)."
)


def _not_found(request: httpx.Request) -> httpx.Response:
    return httpx.Response(404, text="Not Found")


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["get_game_summary", "get_player_stats"])
async def test_summary_404_names_event_id_and_where_to_find_one(method: str) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(_not_found), base_url="https://site.web.api.espn.com"
    ) as hc:
        client = ESPNClient(http_client=hc)
        with pytest.raises(ESPNNotFoundError) as exc_info:
            await getattr(client, method)("baseball", "mlb", "361102103")

    message = str(exc_info.value)
    assert message.startswith(
        "HTTP 404: ESPN resource at 'apis/site/v2/sports/baseball/mlb/summary' not found."
    )
    assert "'361102103'" in message
    assert message.endswith(_EVENT_ID_HINT)
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True


@pytest.mark.asyncio
async def test_summary_404_reaches_client_as_is_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """The hint rides the existing error path: tools/call returns isError with the message."""
    hc = httpx.AsyncClient(
        transport=httpx.MockTransport(_not_found), base_url="https://site.web.api.espn.com"
    )
    monkeypatch.setattr(server, "client", ESPNClient(http_client=hc))
    with pytest.raises(ToolError, match="No event with event_id '361102103' was found"):
        await server.get_game_summary(sport="baseball", league="mlb", event_id="361102103")
    async with Client(create_server(profile="full")) as mcp_client:
        result = await mcp_client.call_tool(
            "games_get_game_summary",
            {"sport": "baseball", "league": "mlb", "event_id": "361102103"},
            raise_on_error=False,
        )
    await hc.aclose()
    assert result.is_error
    text = result.content[0].text  # type: ignore[union-attr]
    assert "No event with event_id '361102103' was found" in text
    assert _EVENT_ID_HINT in text


# ── Error and hint text never names a tool ──────────────────────────────────────
# A model sees only the tools its profile lists or its search found, so error text that
# names another tool can point at one it can't call.

_SRC = Path(espn_mcp.__file__).parent


async def _registered_tool_names() -> set[str]:
    names = {t.name for t in await create_server(profile="full").list_tools()}
    names |= {t.name for t in await create_server(enable_tool_search=True).list_tools()}
    assert {"games_get_scoreboard", "search_tools", "call_tool"} <= names
    return names


def _error_strings() -> list[tuple[str, str]]:
    """String literals the server can put in an error.

    client.py, errors.py and middleware.py hold every error and hint message, so all their
    literals are checked. In server.py and tools/*.py, whose descriptions and prompts name
    tools on purpose, only literals inside raise statements are checked.
    """
    found: list[tuple[str, str]] = []
    for path in sorted([*_SRC.glob("*.py"), *(_SRC / "tools").glob("*.py")]):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        whole_file = path.name in ("client.py", "errors.py", "middleware.py")
        roots = [tree] if whole_file else [n for n in ast.walk(tree) if isinstance(n, ast.Raise)]
        for root in roots:
            for node in ast.walk(root):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    found.append((path.name, node.value))
    return found


@pytest.mark.asyncio
async def test_error_and_hint_strings_name_no_tool() -> None:
    names = await _registered_tool_names()
    strings = _error_strings()
    assert any("Get valid event IDs" in s for _, s in strings)
    for file, text in strings:
        for name in names:
            assert name not in text, f"{file}: {text!r} names {name}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("games_get_game_summary", {"event_id": "361102103"}),
        ("teams_get_player_stats", {"event_id": "361102103"}),
    ],
)
async def test_not_found_result_names_no_tool(
    tool: str, args: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The isError text a client gets for an unknown event ID names no registered tool."""
    names = await _registered_tool_names()
    hc = httpx.AsyncClient(
        transport=httpx.MockTransport(_not_found), base_url="https://site.web.api.espn.com"
    )
    monkeypatch.setattr(server, "client", ESPNClient(http_client=hc))
    async with Client(create_server(profile="full")) as mcp_client:
        result = await mcp_client.call_tool(
            tool, {"sport": "baseball", "league": "mlb", **args}, raise_on_error=False
        )
    await hc.aclose()
    assert result.is_error
    text = result.content[0].text  # type: ignore[union-attr]
    assert _EVENT_ID_HINT in text
    for name in names:
        assert name not in text, name


# ── Futures and power index default to ESPN's current season ────────────────────


def _core_handler(league_doc: Any, seen: list[str]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        path = request.url.path
        if re.fullmatch(r"/v2/sports/[^/]+/leagues/[^/]+", path):
            if isinstance(league_doc, int):
                return httpx.Response(league_doc)
            return httpx.Response(200, json=league_doc)
        if path.endswith(("/futures", "/powerindex")):
            return httpx.Response(200, json={"items": []})
        return httpx.Response(200, json={})

    return handler


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["get_futures", "get_power_index"])
async def test_default_season_is_espn_current_season(method: str) -> None:
    """With no season, the league document's season.year picks the URL (2027 = 2026-27)."""
    seen: list[str] = []
    doc = {"season": {"year": 2027, "displayName": "2026-27"}}
    async with httpx.AsyncClient(transport=httpx.MockTransport(_core_handler(doc, seen))) as hc:
        out = await getattr(ESPNClient(http_client=hc), method)("basketball", "nba")
    endpoint = "futures" if method == "get_futures" else "powerindex"
    assert f"/v2/sports/basketball/leagues/nba/seasons/2027/{endpoint}" in seen
    assert "/v2/sports/basketball/leagues/nba" in seen
    assert out["season"] == 2027


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["get_futures", "get_power_index"])
async def test_explicit_season_skips_league_lookup(method: str) -> None:
    seen: list[str] = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(_core_handler({}, seen))) as hc:
        out = await getattr(ESPNClient(http_client=hc), method)("basketball", "nba", season=2016)
    assert "/v2/sports/basketball/leagues/nba" not in seen
    assert out["season"] == 2016


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "league_doc",
    [
        500,
        {"ok": True},
        {"season": "2027"},
        {"season": {"year": "2027"}},
        {"season": {"year": True}},
    ],
)
async def test_default_season_falls_back_to_calendar_year(
    league_doc: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed or unlabelled league lookup keeps the previous calendar-year default."""
    seen: list[str] = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(_core_handler(league_doc, seen))
    ) as hc:
        client = ESPNClient(http_client=hc, max_retries=0)
        out = await client.get_futures("hockey", "nhl")
    year = datetime.now(timezone.utc).year
    assert f"/v2/sports/hockey/leagues/nhl/seasons/{year}/futures" in seen
    assert out["season"] == year


# ── Tool error wrapper: raised after the except block ───────────────────────────


@pytest.mark.asyncio
async def test_tool_error_carries_no_original_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """The redacted ToolError is raised outside the except block: no cause, no context."""
    from espn_mcp.tools import games, news, teams

    secret = "api_key=supersecretvalue123"

    async def boom(*_: Any, **__: Any) -> Any:
        raise RuntimeError(f"upstream said {secret}")

    for module in (games, news, teams):
        wrapped = module.espn_tool(boom)
        with pytest.raises(ToolError) as exc_info:
            await wrapped()
        assert "supersecretvalue123" not in str(exc_info.value)
        assert "upstream said api_key=" in str(exc_info.value)
        assert exc_info.value.__cause__ is None
        assert exc_info.value.__context__ is None
        assert exc_info.value.__suppress_context__ is True
