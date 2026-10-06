"""End-to-end live testing across all dynamically discovered ESPN MCP tools."""

from __future__ import annotations

import json
import re
import time
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastmcp.tools.base import ToolResult
from mcp.types import CallToolResult, TextContent

from espn_mcp.server import mcp

_BEARER_PATTERN = re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-\.]+")
_PEM_KEY_PATTERN = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----",
    re.MULTILINE,
)
_REDACT_PATTERN = re.compile(
    r"(?i)(password|token|secret|key|private_key|authorization)(['\":\s=]+)(?:\"[^\"]*\"|'[^']*'|[^\s,;'\"]+)"
)

# Long-lived ESPN identifiers. MLB team 10 is the New York Yankees, NFL team 10
# is the Tennessee Titans, and athlete 33192 is Aaron Judge. Event 401569483 is
# the completed 2024-06-13 White Sox at Mariners game, which ESPN still serves
# for summary, box score, odds, plays, situation, probabilities, and predictor.
# Season 2025 is a completed NFL season for draft, futures, and power index.
_MLB = {"sport": "baseball", "league": "mlb"}
_NFL = {"sport": "football", "league": "nfl"}
_EVENT = {**_MLB, "event_id": "401569483"}
_YANKEES = {**_MLB, "team_id": "10"}
_TITANS = {**_NFL, "team_id": "10"}
_JUDGE = {**_MLB, "athlete_id": "33192"}
_NFL_2025 = {**_NFL, "season": 2025}


def _redact_secrets(text: str) -> str:
    """Strip credentials, bearer tokens, and private keys from output strings."""
    redacted = _PEM_KEY_PATTERN.sub("[redacted private key]", text)
    redacted = _BEARER_PATTERN.sub("Bearer [redacted]", redacted)
    return _REDACT_PATTERN.sub(r"\1\2[redacted]", redacted)


SAFE_TOOL_FIXTURES: dict[str, dict[str, Any]] = {
    "games_get_scoreboard": dict(_MLB),
    "games_get_game_summary": dict(_EVENT),
    "games_get_team_schedule": dict(_YANKEES),
    "games_get_standings": dict(_MLB),
    "games_get_rankings": {"sport": "football", "league": "college-football"},
    "games_get_transactions": dict(_MLB),
    "games_get_leaders_by_athlete": dict(_MLB),
    "games_get_leaders_by_team": dict(_MLB),
    "games_get_league_groups": dict(_MLB),
    "games_get_league_events": dict(_MLB),
    "games_get_league_draft": dict(_NFL_2025),
    "games_get_scoreboard_header": dict(_NFL),
    "games_get_event_odds": dict(_EVENT),
    "games_get_play_by_play": dict(_EVENT),
    "games_get_game_situation": dict(_EVENT),
    "games_get_win_probabilities": dict(_EVENT),
    "games_get_game_predictor": dict(_EVENT),
    "games_get_calendar": dict(_MLB),
    "games_get_futures": dict(_NFL_2025),
    "games_get_power_index": dict(_NFL_2025),
    "teams_search": {"query": "Aaron Judge", "type": "player", "limit": 1},
    "teams_list_teams": dict(_MLB),
    "teams_get_team": dict(_YANKEES),
    "teams_get_team_statistics": dict(_YANKEES),
    "teams_get_team_roster": dict(_YANKEES),
    "teams_get_team_depth_chart": dict(_TITANS),
    "teams_get_player_stats": dict(_EVENT),
    "teams_get_athlete_overview": dict(_JUDGE),
    "teams_get_athlete_bio": dict(_JUDGE),
    "teams_get_athlete_stats": dict(_JUDGE),
    "teams_get_athlete_gamelog": dict(_JUDGE),
    "teams_get_athlete_splits": dict(_JUDGE),
    "news_get_news": dict(_MLB),
}

# Tools that cannot be exercised with a stable identifier. The value is the
# per-tool reason (for example, "needs an in-season event id").
SKIPPED_TOOLS: dict[str, str] = {}


def assert_fixture_catalog_covers_tools(tool_names: set[str]) -> None:
    """Require fixture keys plus skip keys to equal the runtime tool list."""
    fixtures = set(SAFE_TOOL_FIXTURES)
    skips = set(SKIPPED_TOOLS)
    overlap = fixtures & skips
    assert not overlap, f"Tools listed as both fixture and skip: {sorted(overlap)}"
    blank = sorted(name for name, reason in SKIPPED_TOOLS.items() if not reason.strip())
    assert not blank, f"Skipped tools need a stated reason: {blank}"
    covered = fixtures | skips
    missing = sorted(tool_names - covered)
    extra = sorted(covered - tool_names)
    assert covered == tool_names, (
        "Fixture and skip keys do not match the server tool list from list_tools. "
        f"missing={missing} extra={extra}"
    )


def _is_tool_call_result(res: Any) -> bool:
    """Accept the MCP wire result and FastMCP's in-process ToolResult."""
    return isinstance(res, (CallToolResult, ToolResult))


def _tool_payload_status(res: Any) -> tuple[str | None, str | None]:
    """Return (status, message) from a tool payload, when the body carries one."""
    structured = getattr(res, "structured_content", None)
    if isinstance(structured, dict) and "status" in structured:
        message = structured.get("message")
        return str(structured["status"]), None if message is None else str(message)
    for block in getattr(res, "content", []) or []:
        text = getattr(block, "text", None)
        if not isinstance(text, str):
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and "status" in data:
            message = data.get("message")
            return str(data["status"]), None if message is None else str(message)
    return None, None


async def dispatch_tool_call(srv: Any, tool_name: str) -> tuple[str, bool, str | None]:
    """Execute a single tool call and return (status, is_error, error_message)."""
    if tool_name not in SAFE_TOOL_FIXTURES:
        return ("FAIL", True, f"No SAFE_TOOL_FIXTURES entry for {tool_name}")
    try:
        args = SAFE_TOOL_FIXTURES[tool_name]
        res = await srv.call_tool(tool_name, args)

        if not _is_tool_call_result(res):
            return ("FAIL", True, f"Expected CallToolResult, got {type(res).__name__}")

        if res.is_error:
            return ("FAIL", True, None)

        payload_status, payload_message = _tool_payload_status(res)
        if payload_status != "success":
            detail = payload_message or f"unexpected tool status: {payload_status!r}"
            return ("FAIL", True, _redact_secrets(detail))
        return ("PASS", False, None)
    except Exception as exc:
        exc_type = type(exc).__name__
        sanitized = _redact_secrets(str(exc))
        return ("FAIL", True, f"{exc_type}: {sanitized}")


@pytest.mark.asyncio
async def test_dispatch_tool_call_offline() -> None:
    """Verify tool invocation logic and dispatch behavior using AsyncMock."""
    mock_srv = AsyncMock()

    # 1. Successful non-destructive tool
    mock_srv.call_tool.return_value = CallToolResult(
        content=[TextContent(type="text", text='{"status": "success"}')],
        is_error=False,
    )
    status, is_err, err = await dispatch_tool_call(mock_srv, "games_get_scoreboard")
    assert status == "PASS"
    assert not is_err
    mock_srv.call_tool.assert_awaited_with(
        "games_get_scoreboard", {"sport": "baseball", "league": "mlb"}
    )

    # 2. Error response with is_error=True
    mock_srv.call_tool.return_value = CallToolResult(
        content=[TextContent(type="text", text='{"status": "error"}')],
        is_error=True,
    )
    status, is_err, err = await dispatch_tool_call(mock_srv, "games_get_scoreboard")
    assert status == "FAIL"
    assert is_err
    assert err is None

    # 3. Unexpected exception raised with secret redaction
    mock_srv.call_tool.side_effect = RuntimeError(
        "failed with Bearer secret.token and password=supersecret123"
    )
    status, is_err, err = await dispatch_tool_call(mock_srv, "games_get_scoreboard")
    assert status == "FAIL"
    assert is_err
    assert err is not None
    assert "supersecret123" not in err
    assert "secret.token" not in err
    assert "[redacted]" in err
    assert "RuntimeError:" in err

    # 4. Non-CallToolResult return value returns failure
    mock_srv.call_tool.side_effect = None
    mock_srv.call_tool.return_value = "plain string output"
    status, is_err, err = await dispatch_tool_call(mock_srv, "games_get_scoreboard")
    assert status == "FAIL"
    assert is_err
    assert "Expected CallToolResult" in (err or "")

    # 5. A tool with no fixture is not invoked with empty arguments
    bare = AsyncMock()
    status, is_err, err = await dispatch_tool_call(bare, "not_a_registered_tool")
    assert status == "FAIL"
    assert is_err
    assert err is not None
    assert "No SAFE_TOOL_FIXTURES entry" in err
    bare.call_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_redacts_secret_in_error_payload() -> None:
    """An is_error=False error payload fails, and the message is redacted."""
    secret = "ghp_fakefaketoken123"
    payload = json.dumps({"status": "error", "message": f"upstream rejected token={secret}"})
    mock_srv = AsyncMock()
    mock_srv.call_tool.return_value = CallToolResult(
        content=[TextContent(type="text", text=payload)],
        is_error=False,
    )
    status, is_err, err = await dispatch_tool_call(mock_srv, "games_get_scoreboard")
    assert status == "FAIL"
    assert is_err
    assert err is not None
    assert secret not in err
    assert "[redacted]" in err


@pytest.mark.asyncio
async def test_dispatch_fails_when_payload_has_no_status() -> None:
    """A successful call whose payload has no status field is a failure."""
    mock_srv = AsyncMock()
    mock_srv.call_tool.return_value = CallToolResult(
        content=[TextContent(type="text", text='{"data": {"count": 1}}')],
        is_error=False,
    )
    status, is_err, err = await dispatch_tool_call(mock_srv, "games_get_scoreboard")
    assert status == "FAIL"
    assert is_err
    assert err is not None
    assert "unexpected tool status" in err


@pytest.mark.asyncio
async def test_dispatch_passes_structured_success_tool_result() -> None:
    """FastMCP ToolResult with structured status success passes."""
    mock_srv = AsyncMock()
    mock_srv.call_tool.return_value = ToolResult(
        structured_content={"status": "success", "data": {"ok": True}},
        is_error=False,
    )
    status, is_err, err = await dispatch_tool_call(mock_srv, "games_get_scoreboard")
    assert status == "PASS"
    assert not is_err
    assert err is None


@pytest.mark.asyncio
async def test_fixture_catalog_matches_server_tools() -> None:
    """Fixture keys and explicit skips must equal list_tools, with no hard-coded count."""
    tools = await mcp.list_tools()
    assert_fixture_catalog_covers_tools({tool.name for tool in tools})


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_all_discovered_tools_live() -> None:
    """Dynamically discover and exercise registered tools against live endpoints.

    Run with ``uv run pytest -m e2e --no-cov``.
    """
    tools = await mcp.list_tools()
    assert len(tools) > 0, "No tools registered in MCPServer"
    tool_names = {tool.name for tool in tools}
    assert_fixture_catalog_covers_tools(tool_names)

    results: list[dict[str, Any]] = []

    for tool in tools:
        t0 = time.perf_counter()
        tool_name = tool.name

        if tool_name in SKIPPED_TOOLS:
            results.append(
                {
                    "tool": tool_name,
                    "status": "SKIP",
                    "reason": SKIPPED_TOOLS[tool_name],
                    "latency_ms": (time.perf_counter() - t0) * 1000,
                    "is_error": False,
                }
            )
            continue

        status, is_err, err_msg = await dispatch_tool_call(mcp, tool_name)
        latency_ms = (time.perf_counter() - t0) * 1000

        res_entry: dict[str, Any] = {
            "tool": tool_name,
            "status": status,
            "latency_ms": latency_ms,
            "is_error": is_err,
        }
        if err_msg:
            res_entry["error"] = err_msg
        results.append(res_entry)

    failed = [r for r in results if r["status"] == "FAIL"]
    passed = sum(1 for r in results if r["status"] == "PASS")
    skipped = sum(1 for r in results if r["status"] == "SKIP")
    print(f"e2e live tools: pass={passed} skip={skipped} fail={len(failed)}")
    assert not failed, f"E2E live tool verification failed for: {failed}"
