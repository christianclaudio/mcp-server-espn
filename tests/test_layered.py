"""Tests for FastMCP 4 Server Composition, profiles, middleware, and domain guards."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastmcp import FastMCP
from fastmcp.server.middleware import MiddlewareContext

from espn_mcp.client import default_client, get_client
from espn_mcp.config import settings
from espn_mcp.errors import SafetyViolationError
from espn_mcp.middleware import (
    GamesDomainGuardMiddleware,
    ParentAuditMiddleware,
    ReadOnlyGateMiddleware,
    TeamsDomainGuardMiddleware,
)
from espn_mcp.server import create_server, main


@pytest.mark.asyncio
async def test_create_server_profiles() -> None:
    """Verify create_server mounts correct domain sub-servers according to profile."""
    # 1. Full profile mounts games, teams, and news
    full_server = create_server(profile="full")
    tools_full = await full_server.list_tools()
    assert len(tools_full) == 33
    names = {t.name for t in tools_full}
    assert "games_get_scoreboard" in names
    assert "teams_get_team_roster" in names
    assert "news_get_news" in names
    assert "teams_search" in names
    assert "games_get_transactions" in names
    assert "teams_get_athlete_bio" in names
    assert "games_get_event_odds" in names

    # 2. Games profile mounts only games
    games_srv = create_server(profile="games")
    tools_games = await games_srv.list_tools()
    assert len(tools_games) == 20
    assert all(t.name.startswith("games_") for t in tools_games)

    # 3. Teams profile mounts only teams
    teams_srv = create_server(profile="teams")
    tools_teams = await teams_srv.list_tools()
    assert len(tools_teams) == 12
    assert all(t.name.startswith("teams_") for t in tools_teams)

    # 4. News profile mounts only news
    news_srv = create_server(profile="news")
    tools_news = await news_srv.list_tools()
    assert len(tools_news) == 1
    assert tools_news[0].name == "news_get_news"

    # 5. Readonly profile lists every tool annotated readOnlyHint=True: all 33 ESPN tools
    ro_srv = create_server(profile="readonly")
    tools_ro = await ro_srv.list_tools()
    assert len(tools_ro) == 33

    # 6. Unknown profile raises instead of building an empty server
    with pytest.raises(ValueError, match="Unknown profile 'custom_empty'"):
        create_server(profile="custom_empty")


@pytest.mark.asyncio
async def test_create_server_tool_search() -> None:
    """Opt-in regex Tool Search on full replaces the flat list with search_tools + call_tool."""
    srv = create_server(enable_tool_search=True)
    assert [t.name for t in await srv.list_tools()] == ["search_tools", "call_tool"]


@pytest.mark.asyncio
async def test_parent_audit_middleware_success() -> None:
    """Verify ParentAuditMiddleware logs success and returns tool result."""
    mw = ParentAuditMiddleware()
    ctx = MagicMock(spec=MiddlewareContext)
    ctx.message = MagicMock()
    ctx.message.name = "test_tool"

    expected_res = MagicMock()

    async def fake_call_next(_ctx: MiddlewareContext) -> MagicMock:
        return expected_res

    res = await mw.on_message(ctx, fake_call_next)
    assert res is expected_res


@pytest.mark.asyncio
async def test_parent_audit_middleware_error() -> None:
    """Verify ParentAuditMiddleware logs error with secret redaction and re-raises."""
    mw = ParentAuditMiddleware()
    ctx = MagicMock(spec=MiddlewareContext)
    ctx.message = MagicMock()
    ctx.message.name = "test_tool"
    ctx.method = "tools/call"

    async def fake_failing_next(_ctx: MiddlewareContext) -> None:
        raise ValueError("Failed with Bearer secret-auth-token-xyz")

    with pytest.raises(ValueError) as exc_info:
        await mw.on_message(ctx, fake_failing_next)

    assert "secret-auth-token-xyz" not in str(exc_info.value)
    assert "[REDACTED]" in str(exc_info.value)


@pytest.mark.asyncio
async def test_read_only_gate_middleware(monkeypatch: pytest.MonkeyPatch) -> None:
    """The gate passes everything when read-only is off and judges readOnlyHint when on."""
    serving = SimpleNamespace(fastmcp=create_server(profile="full"))
    mw = ReadOnlyGateMiddleware()
    called = 0

    async def fake_next(_ctx: MiddlewareContext) -> str:
        nonlocal called
        called += 1
        return "success"

    def ctx(name: str, method: str = "tools/call") -> MiddlewareContext:
        return MiddlewareContext(
            method=method,
            message=SimpleNamespace(name=name, arguments={}),
            fastmcp_context=serving,  # type: ignore[arg-type]
        )

    # Read-only off: every call passes, whatever its name
    monkeypatch.setattr(settings, "MCP_READONLY", False)
    assert await mw.on_message(ctx("games_delete_record"), fake_next) == "success"

    # Read-only on: a real tool annotated readOnlyHint=True passes
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    assert await mw.on_message(ctx("games_get_scoreboard"), fake_next) == "success"
    # An unknown name is not refused by the gate (FastMCP reports Unknown tool)
    assert await mw.on_message(ctx("games_delete_record"), fake_next) == "success"
    # Non tools/call methods pass through
    assert await mw.on_message(ctx("games_delete_record", "tools/list"), fake_next) == "success"
    assert called == 4

    # The readonly profile enforces the gate without the setting
    monkeypatch.setattr(settings, "MCP_READONLY", False)
    enforced = ReadOnlyGateMiddleware(enforce=True, catalog={"games_hidden_write"})
    with pytest.raises(SafetyViolationError, match="'games_hidden_write' blocked"):
        await enforced.on_message(ctx("games_hidden_write"), fake_next)


@pytest.mark.asyncio
async def test_games_domain_guard_middleware() -> None:
    """Verify GamesDomainGuardMiddleware validates query bounds."""
    mw = GamesDomainGuardMiddleware()
    ctx = MagicMock(spec=MiddlewareContext)
    ctx.method = "tools/call"
    ctx.message = MagicMock()

    # Valid limit <= 100
    ctx.message.arguments = {"sport": "baseball", "league": "mlb", "limit": 50}

    async def fake_next(_ctx: MiddlewareContext) -> str:
        return "ok"

    res = await mw.on_message(ctx, fake_next)
    assert res == "ok"

    # Limit > 100 fails
    ctx.message.arguments = {"sport": "baseball", "league": "mlb", "limit": 150}
    with pytest.raises(ValueError) as exc_info:
        await mw.on_message(ctx, fake_next)
    assert "exceeds maximum allowable batch size of 100" in str(exc_info.value)


@pytest.mark.asyncio
async def test_teams_domain_guard_middleware() -> None:
    """Verify TeamsDomainGuardMiddleware validates team and athlete IDs."""
    mw = TeamsDomainGuardMiddleware()
    ctx = MagicMock(spec=MiddlewareContext)
    ctx.method = "tools/call"
    ctx.message = MagicMock()

    async def fake_next(_ctx: MiddlewareContext) -> str:
        return "ok"

    # Valid arguments
    ctx.message.arguments = {"team_id": "23", "athlete_id": "5000"}
    res = await mw.on_message(ctx, fake_next)
    assert res == "ok"

    # Empty team_id fails
    ctx.message.arguments = {"team_id": "   ", "athlete_id": "5000"}
    with pytest.raises(ValueError) as exc_info:
        await mw.on_message(ctx, fake_next)
    assert "team_id cannot be empty or whitespace" in str(exc_info.value)

    # Empty athlete_id fails
    ctx.message.arguments = {"team_id": "23", "athlete_id": ""}
    with pytest.raises(ValueError) as exc_info:
        await mw.on_message(ctx, fake_next)
    assert "athlete_id cannot be empty or whitespace" in str(exc_info.value)


def test_get_client_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify get_client falls back to default_client when srv is unavailable."""
    import espn_mcp.server as srv

    # Active client on srv
    assert get_client() is srv.client

    # When srv has no client attribute
    monkeypatch.delattr(srv, "client", raising=False)
    assert get_client() is default_client

    # When importing srv raises ImportError
    monkeypatch.setitem(sys.modules, "espn_mcp.server", None)
    assert get_client() is default_client


def test_main_cli_profile_and_search(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify CLI accepts --profile and --enable-tool-search flags."""
    run_called = False

    def mock_run(self: FastMCP, *args: object, **kwargs: object) -> None:
        nonlocal run_called
        run_called = True

    monkeypatch.setattr(FastMCP, "run", mock_run)
    monkeypatch.setattr(
        "sys.argv",
        ["espn-mcp", "--profile", "games", "--enable-tool-search"],
    )
    main()
    assert run_called is True
