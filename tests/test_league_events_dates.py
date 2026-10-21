"""League events by date: a date or range is refused, no dates keeps ESPN's /events route.

ESPN's Site v2 ``/events`` route ignores ``dates`` (verified live on 2026-10-21), and the league
scoreboard returns a different event shape, so ``get_league_events`` refuses ``dates`` with an
``isError`` that points to the scoreboard, without any request. Bad dates are still refused with
the scoreboard's validation message.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastmcp import Client

from espn_mcp.client import ESPNClient
from espn_mcp.errors import ESPNValidationError
from espn_mcp.server import mcp

_SITE = "/apis/site/v2/sports"
_CURRENT = {"id": "current", "name": "Game current", "date": "2026-10-21T23:05Z"}


class _Router:
    """MockTransport handler that records each request and answers /events."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.url.path, dict(request.url.params)))
        return httpx.Response(200, json={"events": [_CURRENT]})


def _client(router: _Router) -> ESPNClient:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(router), base_url="https://site.web.api.espn.com"
    )
    return ESPNClient(http_client=http, max_retries=0)


@pytest.mark.asyncio
async def test_no_dates_still_reads_events_route_unchanged() -> None:
    router = _Router()
    client = _client(router)
    res = await client.get_league_events("basketball", "nba")
    assert router.calls == [(f"{_SITE}/basketball/nba/events", {})]
    assert res == {
        "sport": "basketball",
        "league": "nba",
        "dates": None,
        "count": 1,
        "events": [_CURRENT],
    }
    await client.close()


@pytest.mark.asyncio
async def test_empty_dates_is_sent_to_events_route_as_before() -> None:
    router = _Router()
    client = _client(router)
    res = await client.get_league_events("basketball", "nba", dates="")
    assert router.calls == [(f"{_SITE}/basketball/nba/events", {"dates": ""})]
    assert res["dates"] == ""
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sport", "league", "value"),
    [
        ("baseball", "mlb", "20260919"),
        ("baseball", "mlb", "2026-09-19"),
        ("basketball", "nba", "20261020-20261022"),
        ("hockey", "nhl", "20260101-20260131"),  # exactly 31 days
        ("golf", "pga", "20260101-20260301"),  # golf ranges have no day limit
    ],
)
async def test_valid_dates_are_refused_without_a_request(
    sport: str, league: str, value: str
) -> None:
    router = _Router()
    client = _client(router)
    with pytest.raises(ESPNValidationError) as excinfo:
        await client.get_league_events(sport, league, dates=value)
    message = str(excinfo.value)
    assert "Date filtering is not supported on league events" in message
    assert f"from the scoreboard instead, with sport='{sport}', league='{league}', " in message
    assert f"date='{value}'; it returns the full scoreboard event shape" in message
    assert router.calls == []
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sport", "league", "value", "reason"),
    [
        ("football", "nfl", "20260901-20261002", "spans 32 days"),
        ("football", "nfl", "20260920-20260919", "start date is after the end date"),
        ("football", "nfl", "2026091-20260925", "YYYYMMDD"),
        ("football", "nfl", "20250230", "not a real calendar date"),
        ("golf", "pga", "20250230", "not a real calendar date"),
        ("golf", "pga", "20260231-20260301", "not a real calendar date"),
    ],
)
async def test_bad_dates_keep_their_validation_error(
    sport: str, league: str, value: str, reason: str
) -> None:
    router = _Router()
    client = _client(router)
    with pytest.raises(ESPNValidationError) as excinfo:
        await client.get_league_events(sport, league, dates=value)
    assert reason in str(excinfo.value)
    assert "not supported" not in str(excinfo.value)
    assert router.calls == []
    await client.close()


@pytest.mark.asyncio
async def test_tool_reports_dates_as_is_error_and_no_dates_succeeds() -> None:
    async with Client(mcp) as mcp_client:
        current = await mcp_client.call_tool(
            "games_get_league_events", {"sport": "football", "league": "nfl"}
        )
        dated = await mcp_client.call_tool(
            "games_get_league_events",
            {"sport": "basketball", "league": "nba", "dates": "20261020-20261021"},
            raise_on_error=False,
        )
        bad = await mcp_client.call_tool(
            "games_get_league_events",
            {"sport": "basketball", "league": "nba", "dates": "20250230"},
            raise_on_error=False,
        )

    def text(res: Any) -> str:
        return " ".join(getattr(c, "text", "") for c in res.content)

    assert not current.is_error
    assert dated.is_error
    assert "from the scoreboard instead" in text(dated)
    assert "date='20261020-20261021'" in text(dated)
    assert bad.is_error
    assert "not a real calendar date" in text(bad)
    tools = {t.name: t for t in await mcp.list_tools()}
    tool = tools["games_get_league_events"]
    assert "games_get_scoreboard" in (tool.description or "")
    assert "games_get_scoreboard" in tool.parameters["properties"]["dates"]["description"]
