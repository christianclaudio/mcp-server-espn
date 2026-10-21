"""Retry-After handling on 429 and 5xx retries in ``ESPNClient.request``."""

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from typing import Any

import httpx
import pytest

import espn_mcp.client as client_mod
from espn_mcp.client import ESPNClient, _retry_after_seconds
from espn_mcp.errors import ESPNRateLimitError

BASE = "https://site.web.api.espn.com"


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record every retry wait instead of sleeping, with the jitter fixed at 0.3 s."""
    recorded: list[float] = []

    async def fake_sleep(delay: float) -> None:
        recorded.append(delay)

    monkeypatch.setattr(client_mod.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(client_mod.random, "uniform", lambda a, b: 0.3)
    return recorded


async def _run(
    statuses: list[tuple[int, dict[str, str]]],
    *,
    max_retries: int = 3,
    timeout: float | None = None,
) -> tuple[Any, int]:
    """Serve the given (status, headers) responses in order, then 200."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls <= len(statuses):
            status, headers = statuses[calls - 1]
            return httpx.Response(status, headers=headers, text="busy")
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=BASE) as http:
        client = ESPNClient(max_retries=max_retries, timeout=timeout, http_client=http)
        result = await client.request("GET", "retry")
    return result, calls


@pytest.mark.asyncio
async def test_retry_after_seconds_wins_when_longer(sleeps: list[float]) -> None:
    result, calls = await _run([(429, {"Retry-After": "7"})])
    assert result == {"ok": True}
    assert calls == 2
    assert sleeps == [7.0]


@pytest.mark.asyncio
async def test_backoff_wins_when_longer(sleeps: list[float]) -> None:
    result, _ = await _run(
        [(429, {"Retry-After": "0"}), (429, {"Retry-After": "1"}), (429, {"Retry-After": "2"})]
    )
    assert result == {"ok": True}
    # 2**attempt + 0.3 jitter: 1.3, 2.3, 4.3 all exceed the header.
    assert sleeps == [1.3, 2.3, 4.3]


@pytest.mark.asyncio
async def test_retry_after_http_date(sleeps: list[float]) -> None:
    when = datetime.now(timezone.utc) + timedelta(seconds=20)
    await _run([(429, {"Retry-After": format_datetime(when, usegmt=True)})])
    assert len(sleeps) == 1
    assert 17.0 <= sleeps[0] <= 20.0


@pytest.mark.asyncio
async def test_retry_after_past_http_date_falls_back_to_backoff(sleeps: list[float]) -> None:
    when = datetime.now(timezone.utc) - timedelta(hours=1)
    await _run([(429, {"Retry-After": format_datetime(when, usegmt=True)})])
    assert sleeps == [1.3]


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["soon", "-5", "1.5", "+3", "", "Wed, 99 Foo 2026"])
async def test_malformed_or_negative_retry_after_ignored(sleeps: list[float], value: str) -> None:
    await _run([(429, {"Retry-After": value})])
    assert sleeps == [1.3]


@pytest.mark.asyncio
async def test_missing_retry_after_uses_backoff(sleeps: list[float]) -> None:
    await _run([(429, {})])
    assert sleeps == [1.3]


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["100000", "9" * 400], ids=["large", "400-digit"])
async def test_retry_after_capped_at_request_timeout(sleeps: list[float], value: str) -> None:
    await _run([(429, {"Retry-After": value})])
    assert sleeps == [30.0]  # settings.TIMEOUT_SECONDS default


@pytest.mark.asyncio
async def test_retry_after_cap_follows_configured_timeout(sleeps: list[float]) -> None:
    await _run([(429, {"Retry-After": "3600"})], timeout=5.0)
    assert sleeps == [5.0]


@pytest.mark.asyncio
async def test_retry_after_http_date_capped(sleeps: list[float]) -> None:
    when = datetime.now(timezone.utc) + timedelta(days=365)
    await _run([(429, {"Retry-After": format_datetime(when, usegmt=True)})])
    assert sleeps == [30.0]


@pytest.mark.asyncio
async def test_retry_after_honored_on_503(sleeps: list[float]) -> None:
    result, calls = await _run([(503, {"Retry-After": "9"})])
    assert result == {"ok": True}
    assert calls == 2
    assert sleeps == [9.0]


@pytest.mark.asyncio
async def test_retry_count_unchanged_with_retry_after(sleeps: list[float]) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": "8"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=BASE) as http:
        client = ESPNClient(max_retries=2, http_client=http)
        with pytest.raises(ESPNRateLimitError):
            await client.request("GET", "retry")
    assert calls == 3
    assert sleeps == [8.0, 8.0]


def test_retry_after_parser_naive_date_is_utc() -> None:
    # "-0000" makes parsedate_to_datetime return a naive datetime; it is read as UTC.
    response = httpx.Response(429, headers={"Retry-After": "Thu, 01 Jan 2099 00:00:00 -0000"})
    seconds = _retry_after_seconds(response)
    assert seconds is not None and seconds > 0


def test_retry_after_parser_absent() -> None:
    assert _retry_after_seconds(httpx.Response(429)) is None


def test_retry_after_parser_rejects_non_ascii_digits() -> None:
    # A superscript two passes str.isdigit() but is not delta-seconds.
    response = httpx.Response(429, headers={"Retry-After": "\u00b2".encode("latin-1")})
    assert response.headers["Retry-After"] == "\u00b2"
    assert _retry_after_seconds(response) is None
