"""League events by date: dates read the scoreboard, no dates keeps ESPN's /events route.

ESPN's Site v2 ``/events`` route ignores ``dates`` (verified live on 2026-10-21), so a call with
a date or range goes through the scoreboard path, with its range rules, and keeps the
``get_league_events`` result shape.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from fastmcp import Client

import espn_mcp.server as srv
from espn_mcp.client import ESPNClient
from espn_mcp.errors import ESPNValidationError
from espn_mcp.server import mcp

_SITE = "/apis/site/v2/sports"


def _event(event_id: str, day: str) -> dict[str, Any]:
    return {"id": event_id, "name": f"Game {event_id}", "date": f"{day}T23:05Z"}


class _Router:
    """MockTransport handler for /events and /scoreboard that records each request."""

    def __init__(self, fail_dates: frozenset[str] = frozenset()) -> None:
        self.fail_dates = fail_dates
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.calls.append((request.url.path, params))
        if request.url.path.endswith("/events"):
            return httpx.Response(200, json={"events": [_event("current", "20261021")]})
        day = params.get("dates", "")
        if day in self.fail_dates:
            return httpx.Response(400, json={})
        # Every day carries its own game plus one that also appears on the next day.
        return httpx.Response(
            200,
            json={"events": [_event(f"ev-{day}", day), _event("shared", day)], "day": {}},
        )


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
        "events": [_event("current", "20261021")],
    }
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["20260919", "2026-09-19"])
async def test_single_date_reads_the_scoreboard_once(value: str) -> None:
    router = _Router()
    client = _client(router)
    res = await client.get_league_events("baseball", "mlb", dates=value)
    assert router.calls == [(f"{_SITE}/baseball/mlb/scoreboard", {"dates": "20260919"})]
    assert res["dates"] == value
    assert res["count"] == 2
    assert [e["id"] for e in res["events"]] == ["ev-20260919", "shared"]
    await client.close()


@pytest.mark.asyncio
async def test_range_reads_each_day_and_dedupes() -> None:
    router = _Router()
    client = _client(router)
    res = await client.get_league_events("basketball", "nba", dates="20261020-20261022")
    assert router.calls == [
        (f"{_SITE}/basketball/nba/scoreboard", {"dates": day})
        for day in ("20261020", "20261021", "20261022")
    ]
    assert res["dates"] == "20261020-20261022"
    assert [e["id"] for e in res["events"]] == [
        "ev-20261020",
        "shared",
        "ev-20261021",
        "ev-20261022",
    ]
    assert res["count"] == 4
    await client.close()


@pytest.mark.asyncio
async def test_golf_range_is_one_scoreboard_request() -> None:
    router = _Router()
    client = _client(router)
    await client.get_league_events("golf", "pga", dates="20260101-20260301")
    assert router.calls == [(f"{_SITE}/golf/pga/scoreboard", {"dates": "20260101-20260301"})]
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sport", "league", "value"),
    [
        ("football", "nfl", "20260901-20261002"),  # 32 days
        ("football", "nfl", "20260920-20260919"),
        ("football", "nfl", "2026091-20260925"),
        ("football", "nfl", "20250230"),
        ("golf", "pga", "20250230"),
        ("golf", "pga", "20260231-20260301"),
    ],
)
async def test_bad_dates_are_refused_before_any_request(
    sport: str, league: str, value: str
) -> None:
    router = _Router()
    client = _client(router)
    with pytest.raises(ESPNValidationError):
        await client.get_league_events(sport, league, dates=value)
    assert router.calls == []
    await client.close()


@pytest.mark.asyncio
async def test_range_stops_on_the_first_failed_day() -> None:
    router = _Router(fail_dates=frozenset({"20261002"}))
    client = _client(router)
    with pytest.raises(ESPNValidationError, match="HTTP 400"):
        await client.get_league_events("football", "nfl", dates="20261001-20261010")
    assert [params["dates"] for _, params in router.calls] == ["20261001", "20261002"]
    current = asyncio.current_task()
    assert not {task for task in asyncio.all_tasks() if task is not current}
    await client.close()


@pytest.mark.asyncio
async def test_range_keeps_at_most_four_days_in_flight() -> None:
    in_flight = 0
    peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return httpx.Response(200, json={"events": []})

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://site.web.api.espn.com"
    )
    client = ESPNClient(http_client=http, max_retries=0)
    res = await client.get_league_events("hockey", "nhl", dates="20260101-20260131")
    assert res["count"] == 0
    assert peak == 4
    await client.close()


@pytest.mark.asyncio
async def test_dates_result_has_the_same_shape_as_events_route() -> None:
    """Field-by-field shape check against the shared conftest mocks for both routes."""
    current = await srv.get_league_events(sport="football", league="nfl")
    dated = await srv.get_league_events(sport="football", league="nfl", dates="20260927")
    ranged = await srv.get_league_events(sport="football", league="nfl", dates="20260927-20260928")
    for res in (current, dated, ranged):
        assert res["status"] == "success"
    base = current["data"]
    for data in (dated["data"], ranged["data"]):
        assert data.keys() == base.keys() == {"sport", "league", "dates", "count", "events"}
        for key in ("sport", "league", "count", "events"):
            assert type(data[key]) is type(base[key])
        assert isinstance(data["dates"], str)
        assert data["count"] == len(data["events"])
        # The /events mock event's fields (id, name, date) are all on the scoreboard event,
        # with the same types; the scoreboard event adds status and competitions.
        for ev in data["events"]:
            for field, value in base["events"][0].items():
                assert type(ev[field]) is type(value), field
    assert dated["data"]["events"][0]["id"] == "401816789"
    assert ranged["data"]["count"] == 1  # the same event on both days, kept once


@pytest.mark.asyncio
async def test_tool_reports_bad_dates_as_is_error() -> None:
    async with Client(mcp) as mcp_client:
        ok = await mcp_client.call_tool(
            "games_get_league_events",
            {"sport": "basketball", "league": "nba", "dates": "20261020-20261021"},
        )
        bad = await mcp_client.call_tool(
            "games_get_league_events",
            {"sport": "basketball", "league": "nba", "dates": "20250230"},
            raise_on_error=False,
        )
    assert not ok.is_error
    assert bad.is_error
    assert "not a real calendar date" in " ".join(getattr(c, "text", "") for c in bad.content)
    tools = {t.name: t for t in await mcp.list_tools()}
    dates_schema = tools["games_get_league_events"].parameters["properties"]["dates"]
    assert "YYYYMMDD-YYYYMMDD" in dates_schema["description"]
