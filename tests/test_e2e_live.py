"""End-to-end live testing across all dynamically discovered ESPN MCP tools."""

from __future__ import annotations

import re
import time
from typing import Any
from unittest.mock import AsyncMock

import pytest
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


def _redact_secrets(text: str) -> str:
    """Strip credentials, bearer tokens, and private keys from output strings."""
    redacted = _PEM_KEY_PATTERN.sub("[redacted private key]", text)
    redacted = _BEARER_PATTERN.sub("Bearer [redacted]", redacted)
    return _REDACT_PATTERN.sub(r"\1\2[redacted]", redacted)


SAFE_TOOL_FIXTURES: dict[str, dict[str, Any]] = {
    "get_scoreboard": {"sport": "baseball", "league": "mlb"},
    "get_game_summary": {"sport": "baseball", "league": "mlb", "event_id": "401569483"},
    "get_player_stats": {"sport": "baseball", "league": "mlb", "event_id": "401569483"},
    "get_standings": {"sport": "baseball", "league": "mlb"},
    "get_team_roster": {"sport": "baseball", "league": "mlb", "team_id": "10"},
    "get_team_schedule": {"sport": "baseball", "league": "mlb", "team_id": "10"},
    "get_athlete_overview": {"sport": "baseball", "league": "mlb", "athlete_id": "33192"},
    "get_league_news": {"sport": "baseball", "league": "mlb"},
    "get_rankings": {"sport": "football", "league": "college-football"},
    "get_supported_sports_and_leagues": {},
}


async def dispatch_tool_call(srv: Any, tool_name: str) -> tuple[str, bool, str | None]:
    """Execute a single tool call and return (status, is_error, error_message)."""
    try:
        args = SAFE_TOOL_FIXTURES.get(tool_name, {})
        res = await srv.call_tool(tool_name, args)

        if not isinstance(res, CallToolResult):
            return ("FAIL", True, f"Expected CallToolResult, got {type(res).__name__}")

        is_err = res.is_error
        status = "FAIL" if is_err else "PASS"
        return (status, is_err, None)
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
    status, is_err, err = await dispatch_tool_call(mock_srv, "get_scoreboard")
    assert status == "PASS"
    assert not is_err
    assert err is None
    mock_srv.call_tool.assert_awaited_with("get_scoreboard", {"sport": "baseball", "league": "mlb"})

    # 2. Error response with is_error=True
    mock_srv.call_tool.return_value = CallToolResult(
        content=[TextContent(type="text", text='{"status": "error"}')],
        is_error=True,
    )
    status, is_err, err = await dispatch_tool_call(mock_srv, "get_scoreboard")
    assert status == "FAIL"
    assert is_err
    assert err is None

    # 3. Unexpected exception raised with secret redaction
    mock_srv.call_tool.side_effect = RuntimeError(
        "failed with Bearer secret.token and password=supersecret123"
    )
    status, is_err, err = await dispatch_tool_call(mock_srv, "get_scoreboard")
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
    status, is_err, err = await dispatch_tool_call(mock_srv, "get_scoreboard")
    assert status == "FAIL"
    assert is_err
    assert "Expected CallToolResult" in (err or "")


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_all_discovered_tools_live() -> None:
    """Dynamically discover and exercise registered tools against live endpoints."""
    tools = await mcp.list_tools()
    assert len(tools) > 0, "No tools registered in MCPServer"

    results: list[dict[str, Any]] = []

    for tool in tools:
        t0 = time.perf_counter()
        tool_name = tool.name

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
    assert not failed, f"E2E live tool verification failed for: {failed}"
