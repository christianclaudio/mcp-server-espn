"""Scoreboard date ranges: single dates unchanged, daily fan-out, golf pass-through, bad ranges.

ESPN's range form is ``dates=YYYYMMDD-YYYYMMDD``. The community reference
(https://github.com/pseudo-r/Public-ESPN-API, "Date-range regression, verified 2026-09-30",
issue #23) reports HTTP 400 for it on the Site v2 MLB and NFL scoreboards and every other team
sport tested, with golf the exception, and one request per day, merged and deduplicated by event
id, as the workaround for ranges up to 31 days.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

import espn_mcp.server as srv
from espn_mcp.client import (
    KNOWN_SPORTS,
    SCOREBOARD_MAX_RANGE_DAYS,
    SCOREBOARD_RANGE_PASSTHROUGH_SPORTS,
    SPORT_LEAGUE_MAP,
    ESPNClient,
    parse_scoreboard_date_range,
)
from espn_mcp.errors import ESPNValidationError
from espn_mcp.server import mcp


def _event(event_id: str | None, day: str) -> dict[str, Any]:
    ev: dict[str, Any] = {
        "name": f"Game {event_id} on {day}",
        "date": f"{day[:4]}-{day[4:6]}-{day[6:]}T23:05Z",
        "status": {"type": {"state": "post", "shortDetail": "Final"}},
        "competitions": [],
    }
    if event_id is not None:
        ev["id"] = event_id
    return ev


class _Recorder:
    """MockTransport handler that records each scoreboard request's query parameters."""

    def __init__(self, pages: dict[str, Any] | None = None, status: int = 200) -> None:
        self.pages = pages or {}
        self.status = status
        self.calls: list[dict[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.calls.append({"path": request.url.path, **params})
        if self.status != 200:
            return httpx.Response(self.status, json={})
        dates = params.get("dates", "")
        page = self.pages.get(dates, {"events": [_event(f"ev-{dates}", dates[:8] or "20260101")]})
        return httpx.Response(200, json=page)


def _client(recorder: _Recorder) -> ESPNClient:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(recorder), base_url="https://site.web.api.espn.com"
    )
    return ESPNClient(http_client=http, max_retries=0)


# --- parse_scoreboard_date_range -------------------------------------------------------------


@pytest.mark.parametrize("value", ["20260919", "2026-09-19", "2026", "today"])
def test_single_date_values_are_not_ranges(value: str) -> None:
    assert parse_scoreboard_date_range(value) is None


def test_range_parses_inclusive_ends() -> None:
    start, end = parse_scoreboard_date_range("20260919-20260925") or (None, None)
    assert (str(start), str(end)) == ("2026-09-19", "2026-09-25")
    same = parse_scoreboard_date_range("20260919-20260919")
    assert same is not None and same[0] == same[1]


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("20260925-20260919", "start date is after the end date"),
        ("20260230-20260301", "Invalid start date '20260230'"),
        ("20260901-20261301", "Invalid end date '20261301'"),
        ("2026091-20260925", "use YYYYMMDD-YYYYMMDD"),
        ("20260919-", "use YYYYMMDD-YYYYMMDD"),
        ("-20260919", "use YYYYMMDD-YYYYMMDD"),
        ("2026-0919", "use YYYYMMDD-YYYYMMDD"),
        ("abcdefgh-20260919", "use YYYYMMDD-YYYYMMDD"),
        (" 20260919-20260925", "use YYYYMMDD-YYYYMMDD"),
    ],
)
def test_bad_ranges_are_rejected(value: str, message: str) -> None:
    with pytest.raises(ESPNValidationError, match=message):
        parse_scoreboard_date_range(value)


# --- client.get_scoreboard -------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(("value", "sent"), [("20260919", "20260919"), ("2026-09-19", "20260919")])
async def test_single_date_call_unchanged(value: str, sent: str) -> None:
    recorder = _Recorder()
    client = _client(recorder)
    sb = await client.get_scoreboard("baseball", "mlb", dates=value, group="80")
    assert recorder.calls == [
        {
            "path": "/apis/site/v2/sports/baseball/mlb/scoreboard",
            "limit": "50",
            "dates": sent,
            "groups": "80",
        }
    ]
    assert sb["count"] == 1
    assert "date_range" not in sb
    await client.close()


@pytest.mark.asyncio
async def test_no_date_sends_no_dates_param() -> None:
    recorder = _Recorder()
    client = _client(recorder)
    sb = await client.get_scoreboard("football", "nfl")
    assert recorder.calls == [
        {"path": "/apis/site/v2/sports/football/nfl/scoreboard", "limit": "50"}
    ]
    assert "date_range" not in sb
    await client.close()


@pytest.mark.asyncio
async def test_mlb_range_fetches_each_day_and_dedupes_events() -> None:
    pages = {
        "20260919": {"events": [_event("1", "20260919"), _event("2", "20260919")]},
        # A suspended game carried into the next day appears on both days.
        "20260920": {"events": [_event("2", "20260920"), _event("3", "20260920")]},
        "20260921": {"events": [_event(None, "20260921"), "not-an-event"]},
    }
    recorder = _Recorder(pages)
    client = _client(recorder)
    sb = await client.get_scoreboard(
        "mlb", "mlb", dates="20260919-20260921", season_type=2, limit=100
    )
    assert [c["dates"] for c in recorder.calls] == ["20260919", "20260920", "20260921"]
    assert all(c["limit"] == "100" and c["seasontype"] == "2" for c in recorder.calls)
    assert all(c["path"] == "/apis/site/v2/sports/baseball/mlb/scoreboard" for c in recorder.calls)
    assert [e["event_id"] for e in sb["events"]] == ["1", "2", "3", None]
    assert sb["events"][1]["date"].startswith("2026-09-19")
    assert sb["count"] == 4
    assert sb["date_range"] == {"start": "20260919", "end": "20260921"}
    await client.close()


@pytest.mark.asyncio
async def test_nfl_range_fetches_each_day_and_skips_missing_events_list() -> None:
    pages = {
        "20260913": {"events": [_event("401", "20260913")]},
        "20260914": {"events": None},
        "20260915": {},
    }
    recorder = _Recorder(pages)
    client = _client(recorder)
    sb = await client.get_scoreboard("football", "nfl", dates="20260913-20260915", week=2)
    assert [c["dates"] for c in recorder.calls] == ["20260913", "20260914", "20260915"]
    assert all(c["week"] == "2" for c in recorder.calls)
    assert [e["event_id"] for e in sb["events"]] == ["401"]
    assert sb["date_range"] == {"start": "20260913", "end": "20260915"}
    await client.close()


@pytest.mark.asyncio
async def test_same_day_range_makes_one_request_for_mlb() -> None:
    recorder = _Recorder()
    client = _client(recorder)
    sb = await client.get_scoreboard("baseball", "mlb", dates="20260919-20260919")
    assert [c["dates"] for c in recorder.calls] == ["20260919"]
    assert sb["date_range"] == {"start": "20260919", "end": "20260919"}
    await client.close()


@pytest.mark.asyncio
async def test_mlb_range_at_limit_and_over_limit() -> None:
    recorder = _Recorder()
    client = _client(recorder)
    sb = await client.get_scoreboard("baseball", "mlb", dates="20260801-20260831")
    assert len(recorder.calls) == SCOREBOARD_MAX_RANGE_DAYS == 31
    assert sb["count"] == 31

    recorder.calls.clear()
    with pytest.raises(ESPNValidationError, match="spans 32 days"):
        await client.get_scoreboard("baseball", "mlb", dates="20260801-20260901")
    assert recorder.calls == []
    await client.close()


def test_only_golf_passes_ranges_through() -> None:
    assert SCOREBOARD_RANGE_PASSTHROUGH_SPORTS == {"golf"}
    assert SCOREBOARD_RANGE_PASSTHROUGH_SPORTS <= KNOWN_SPORTS


_FAN_OUT_ALIASES = sorted(
    alias for alias, (sport, _) in SPORT_LEAGUE_MAP.items() if sport != "golf"
)
_GOLF_ALIASES = sorted(alias for alias, (sport, _) in SPORT_LEAGUE_MAP.items() if sport == "golf")


@pytest.mark.asyncio
@pytest.mark.parametrize("alias", _FAN_OUT_ALIASES)
async def test_every_non_golf_mapped_league_fetches_ranges_per_day(alias: str) -> None:
    sport, league = SPORT_LEAGUE_MAP[alias]
    recorder = _Recorder()
    client = _client(recorder)
    sb = await client.get_scoreboard(alias, alias, dates="20260101-20260102")
    assert [c["dates"] for c in recorder.calls] == ["20260101", "20260102"]
    assert all(
        c["path"] == f"/apis/site/v2/sports/{sport}/{league}/scoreboard" for c in recorder.calls
    )
    assert sb["date_range"] == {"start": "20260101", "end": "20260102"}
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sport", "league"),
    [
        ("basketball", "nba"),
        ("basketball", "wnba"),
        ("basketball", "mens-college-basketball"),
        ("football", "college-football"),
        ("hockey", "nhl"),
        ("soccer", "usa.1"),
        ("baseball", "college-baseball"),
        ("mma", "ufc"),
        ("tennis", "atp"),
        ("racing", "f1"),
    ],
)
async def test_non_golf_range_fans_out_and_dedupes(sport: str, league: str) -> None:
    pages = {
        "20260301": {"events": [_event("a", "20260301"), _event("b", "20260301")]},
        "20260302": {"events": [_event("b", "20260302"), _event("c", "20260302")]},
    }
    recorder = _Recorder(pages)
    client = _client(recorder)
    sb = await client.get_scoreboard(sport, league, dates="20260301-20260302", group="50")
    assert recorder.calls == [
        {
            "path": f"/apis/site/v2/sports/{sport}/{league}/scoreboard",
            "limit": "50",
            "dates": day,
            "groups": "50",
        }
        for day in ("20260301", "20260302")
    ]
    assert [e["event_id"] for e in sb["events"]] == ["a", "b", "c"]
    await client.close()


@pytest.mark.asyncio
async def test_nba_range_over_31_days_is_refused() -> None:
    recorder = _Recorder()
    client = _client(recorder)
    with pytest.raises(ESPNValidationError, match="limited to 31 days"):
        await client.get_scoreboard("basketball", "nba", dates="20260101-20260301")
    assert recorder.calls == []
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("alias", [*_GOLF_ALIASES, "golf/eur"])
async def test_golf_range_is_sent_in_espn_form(alias: str) -> None:
    league = alias.split("/")[1] if "/" in alias else SPORT_LEAGUE_MAP[alias][1]
    sport_arg, league_arg = ("golf", league) if "/" in alias else (alias, alias)
    recorder = _Recorder()
    client = _client(recorder)
    # Longer than 31 days: golf is not fanned out, so no day limit applies.
    sb = await client.get_scoreboard(sport_arg, league_arg, dates="20260101-20260301")
    assert recorder.calls == [
        {
            "path": f"/apis/site/v2/sports/golf/{league}/scoreboard",
            "limit": "50",
            "dates": "20260101-20260301",
        }
    ]
    assert sb["date_range"] == {"start": "20260101", "end": "20260301"}
    await client.close()


@pytest.mark.asyncio
async def test_golf_single_date_unchanged() -> None:
    recorder = _Recorder()
    client = _client(recorder)
    sb = await client.get_scoreboard("golf", "pga", dates="2026-04-12")
    assert recorder.calls == [
        {"path": "/apis/site/v2/sports/golf/pga/scoreboard", "limit": "50", "dates": "20260412"}
    ]
    assert "date_range" not in sb
    await client.close()


@pytest.mark.asyncio
async def test_bad_range_makes_no_request() -> None:
    recorder = _Recorder()
    client = _client(recorder)
    for bad in ("20260925-20260919", "20260231-20260301", "2026091-20260925"):
        with pytest.raises(ESPNValidationError):
            await client.get_scoreboard("football", "nfl", dates=bad)
        with pytest.raises(ESPNValidationError):
            await client.get_scoreboard("golf", "pga", dates=bad)
    assert recorder.calls == []
    await client.close()


@pytest.mark.asyncio
async def test_range_day_failure_fails_the_call() -> None:
    recorder = _Recorder(status=400)
    client = _client(recorder)
    with pytest.raises(ESPNValidationError, match="HTTP 400"):
        await client.get_scoreboard("baseball", "mlb", dates="20260919-20260920")
    await client.close()


# --- tool layer ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_accepts_range_and_reports_bad_range_as_is_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder()
    client = _client(recorder)
    monkeypatch.setattr(srv, "client", client)
    monkeypatch.setattr("espn_mcp.client.default_client", client)

    ok = await srv.get_scoreboard(sport="baseball", league="mlb", date="20260919-20260920")
    assert ok["status"] == "success"
    assert ok["data"]["date_range"] == {"start": "20260919", "end": "20260920"}
    with pytest.raises(ToolError, match="start date is after the end date"):
        await srv.get_scoreboard(sport="baseball", league="mlb", date="20260920-20260919")

    async with Client(mcp) as mcp_client:
        res = await mcp_client.call_tool(
            "games_get_scoreboard",
            {"sport": "football", "league": "nfl", "date": "20260913-20260914"},
        )
        assert not res.is_error
        bad = await mcp_client.call_tool(
            "games_get_scoreboard",
            {"sport": "football", "league": "nfl", "date": "20260913-2026091"},
            raise_on_error=False,
        )
    assert bad.is_error
    assert "YYYYMMDD-YYYYMMDD" in " ".join(getattr(c, "text", "") for c in bad.content)
    await client.close()


@pytest.mark.asyncio
async def test_tool_schema_documents_range_form() -> None:
    tools = {t.name: t for t in await mcp.list_tools()}
    date_schema = tools["games_get_scoreboard"].parameters["properties"]["date"]
    assert "YYYYMMDD-YYYYMMDD" in date_schema["description"]
