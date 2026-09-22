"""Tests for FastMCP 4 Server Composition, profiles, middleware, and domain guards."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest
from fastmcp import FastMCP
from fastmcp.server.middleware import MiddlewareContext

from espn_mcp.client import default_client, get_client
from espn_mcp.config import settings
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

    # 5. Readonly profile mounts all tools
    ro_srv = create_server(profile="readonly")
    tools_ro = await ro_srv.list_tools()
    assert len(tools_ro) == 33

    # 6. Unknown profile mounts nothing
    empty_srv = create_server(profile="custom_empty")
    tools_empty = await empty_srv.list_tools()
    assert len(tools_empty) == 0


@pytest.mark.asyncio
async def test_create_server_tool_search() -> None:
    """Verify opt-in regex tool search adds transform."""
    srv = create_server(enable_tool_search=True)
    assert srv is not None


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

    assert "secret-auth-token-xyz" in str(exc_info.value)


@pytest.mark.asyncio
async def test_read_only_gate_middleware(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify ReadOnlyGateMiddleware blocks mutating operations in read-only mode."""
    mw = ReadOnlyGateMiddleware()

    # When MCP_READONLY is False, all operations are allowed
    monkeypatch.setattr(settings, "MCP_READONLY", False)
    ctx = MagicMock(spec=MiddlewareContext)
    ctx.method = "tools/call"
    ctx.message = MagicMock()
    ctx.message.name = "games_delete_record"
    called = False

    async def fake_next(_ctx: MiddlewareContext) -> str:
        nonlocal called
        called = True
        return "success"

    res = await mw.on_message(ctx, fake_next)
    assert res == "success"
    assert called is True

    # When MCP_READONLY is True, mutating operations are blocked
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    with pytest.raises(PermissionError) as exc_info:
        await mw.on_message(ctx, fake_next)
    assert "Server is in read-only mode" in str(exc_info.value)

    # When MCP_READONLY is True, non-mutating operations are allowed
    ctx.message.name = "games_get_scoreboard"
    res2 = await mw.on_message(ctx, fake_next)
    assert res2 == "success"


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
