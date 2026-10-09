"""Tests for job allowlist profiles, domain-mount profiles, and the readOnlyHint gate."""

from __future__ import annotations

import builtins
import logging
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import NotFoundError, ToolError
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.server.transforms.search import RegexSearchTransform
from mcp.types import ToolAnnotations

from espn_mcp import middleware, profiles, server
from espn_mcp.config import settings
from espn_mcp.errors import SafetyViolationError
from espn_mcp.middleware import ReadOnlyGateMiddleware
from espn_mcp.profiles import (
    FULL_ONLY_TOOLS,
    PROFILES,
    Profile,
    ReadOnlyToolFilter,
    is_read_only_tool,
)
from espn_mcp.server import create_server
from scripts.check_tool_contract import EXPECTED_FULL_ONLY, EXPECTED_PROFILE_COUNTS

JOB_PROFILES = [p for p in PROFILES.values() if p.is_allowlist]
GAMEDAY = PROFILES["gameday"]
SCOREBOARD_ARGS = {"sport": "baseball", "league": "mlb", "date": "20260904"}


@pytest.fixture(autouse=True)
def _offline_client(mock_transport: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Read-only off, default discovery settings, and an offline ESPN client."""
    monkeypatch.setattr(settings, "MCP_READONLY", False)
    monkeypatch.setattr(settings, "MCP_PROFILE", "full")
    monkeypatch.setattr(settings, "MCP_ENABLE_TOOL_SEARCH", False)
    monkeypatch.setattr(settings, "MCP_ENABLE_CODE_MODE", False)
    monkeypatch.setattr(settings, "MCP_TOOL_SEARCH_BACKEND", "regex")
    http = httpx.AsyncClient(transport=mock_transport, base_url="https://site.web.api.espn.com")
    monkeypatch.setattr(server, "client", server.ESPNClient(http_client=http))
    yield


async def _tool_names(app: FastMCP) -> set[str]:
    return {t.name for t in await app.list_tools()}


async def _read_only_names(app: FastMCP) -> set[str]:
    return {t.name for t in await app.list_tools() if is_read_only_tool(t)}


# ---------------------------------------------------------------------------
# Profile counts and catalog placement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(("profile", "expected"), sorted(EXPECTED_PROFILE_COUNTS.items()))
async def test_profile_counts(profile: str, expected: tuple[int, int]) -> None:
    """Every profile builds with exactly its listed and read-only tool counts."""
    app = create_server(profile=profile)
    assert (len(await _tool_names(app)), len(await _read_only_names(app))) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", sorted(EXPECTED_PROFILE_COUNTS))
async def test_profile_counts_compose_with_readonly_setting(
    monkeypatch: pytest.MonkeyPatch, profile: str
) -> None:
    """ESPN_MCP_READONLY=1 on any profile keeps exactly that profile's read-only tools."""
    plain = await _read_only_names(create_server(profile=profile))
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    names = await _tool_names(create_server(profile=profile))
    assert names == plain
    assert len(names) == EXPECTED_PROFILE_COUNTS[profile][1]


@pytest.mark.asyncio
async def test_full_is_exhaustive_and_every_tool_is_placed() -> None:
    """full lists every tool; each tool is in a job profile or marked full-only."""
    full_names = await _tool_names(create_server(profile="full"))
    in_jobs = set().union(*(p.tools or frozenset() for p in JOB_PROFILES))
    assert in_jobs | FULL_ONLY_TOOLS == full_names
    assert not in_jobs & FULL_ONLY_TOOLS


def test_full_only_tools() -> None:
    """Every ESPN tool is in at least one job profile, so no tool is full-only."""
    assert FULL_ONLY_TOOLS == EXPECTED_FULL_ONLY == frozenset()
    assert server.FULL_ONLY_TOOLS is FULL_ONLY_TOOLS


def test_every_profile_has_a_one_line_job() -> None:
    """Each profile documents the job it serves in a single line."""
    for profile in PROFILES.values():
        assert profile.job.strip()
        assert "\n" not in profile.job


@pytest.mark.asyncio
async def test_every_tool_has_explicit_read_only_hint() -> None:
    """Every ESPN tool is a read annotated readOnlyHint=True; none leave it unset."""
    tools = await create_server(profile="full").list_tools()
    missing = sorted(
        t.name for t in tools if t.annotations is None or t.annotations.read_only_hint is None
    )
    assert missing == []
    assert all(is_read_only_tool(t) for t in tools)
    assert len(tools) == 33


# ---------------------------------------------------------------------------
# Profile kinds: domain mounts and allowlists coexist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_allowlist_profile_exposes_exactly_its_tools() -> None:
    """gameday exposes exactly its allowlist, spanning the games, teams and news domains."""
    assert GAMEDAY.tools is not None
    names = await _tool_names(create_server(profile="gameday"))
    assert names == set(GAMEDAY.tools)
    assert {n.split("_", 1)[0] for n in names} == {"games", "teams", "news"}


@pytest.mark.asyncio
async def test_domain_mount_and_allowlist_profiles_coexist() -> None:
    """A domain-mount profile (teams) and an allowlist profile (gameday) both build."""
    assert not PROFILES["teams"].is_allowlist
    assert GAMEDAY.is_allowlist
    team_names = await _tool_names(create_server(profile="teams"))
    gameday_names = await _tool_names(create_server(profile="gameday"))
    assert "teams_get_athlete_bio" in team_names
    assert "teams_get_athlete_bio" not in gameday_names
    assert "games_get_play_by_play" in gameday_names


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", [p.name for p in JOB_PROFILES])
async def test_allowlist_keeps_prompts_and_resources(profile: str) -> None:
    """Allowlist filtering is tools-only: prompts and resources match the full profile."""
    full = create_server(profile="full")
    job = create_server(profile=profile)
    job_prompts = {p.name for p in await job.list_prompts()}
    assert job_prompts == {p.name for p in await full.list_prompts()}
    assert job_prompts == {"games_game_analysis", "teams_team_evaluation"}
    job_resources = {str(r.uri) for r in await job.list_resources()}
    assert job_resources == {str(r.uri) for r in await full.list_resources()}
    assert job_resources == {
        "espn://news/reference/capabilities",
        "espn://news/reference/supported-leagues",
    }


@pytest.mark.asyncio
async def test_allowlist_hides_tools_outside_the_list() -> None:
    """A tool outside the allowlist cannot be called on that profile."""
    app = create_server(profile="gameday")
    with pytest.raises(NotFoundError, match="Unknown tool"):
        await app.call_tool("teams_get_athlete_bio", {"sport": "football", "league": "nfl"})
    res = await app.call_tool("games_get_scoreboard", SCOREBOARD_ARGS)
    assert not res.is_error


def test_unknown_allowlist_name_fails_at_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every allowlisted name must exist in the full catalog; no silent drop."""
    broken = Profile(
        name="broken",
        job="Test only.",
        tools=frozenset({"games_get_scoreboard", "games_nonexistent", "espn_get_scoreboard"}),
    )
    monkeypatch.setitem(profiles.PROFILES, "broken", broken)
    with pytest.raises(ValueError, match="espn_get_scoreboard, games_nonexistent"):
        create_server(profile="broken")


def test_unknown_profile_fails_at_build() -> None:
    """An unknown profile name raises instead of building an empty server."""
    with pytest.raises(ValueError, match="Unknown profile 'nope'; valid profiles: betting"):
        create_server(profile="nope")


def test_unknown_profile_from_settings_fails_at_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """ESPN_MCP_PROFILE is validated the same way as the argument."""
    monkeypatch.setattr(settings, "MCP_PROFILE", "espn")
    with pytest.raises(ValueError, match="Unknown profile 'espn'"):
        create_server()


@pytest.mark.asyncio
async def test_profile_names_are_case_insensitive() -> None:
    """create_server accepts profile names in any case."""
    assert GAMEDAY.tools is not None
    assert await _tool_names(create_server(profile="GameDay")) == set(GAMEDAY.tools)


@pytest.mark.parametrize("flag", ["scouting", "Scouting"])
def test_main_accepts_allowlist_profile(monkeypatch: pytest.MonkeyPatch, flag: str) -> None:
    """--profile scouting is a valid CLI choice, case-insensitively."""
    fake_run = MagicMock()
    monkeypatch.setattr(FastMCP, "run", fake_run)
    monkeypatch.setattr("sys.argv", ["espn-mcp", "--profile", flag])
    server.main()
    fake_run.assert_called_once()


# ---------------------------------------------------------------------------
# Discovery stays full-only on curated profiles
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["gameday", "games", "readonly"])
async def test_curated_profile_with_discovery_stays_flat(
    caplog: pytest.LogCaptureFixture, profile: str
) -> None:
    """Requesting Tool Search or Code Mode on a curated profile logs and stays flat."""
    with caplog.at_level(logging.WARNING):
        searched = create_server(profile=profile, enable_tool_search=True)
        coded = create_server(profile=profile, enable_code_mode=True)
    expected = await _tool_names(create_server(profile=profile))
    assert await _tool_names(searched) == expected
    assert await _tool_names(coded) == expected
    assert not expected & {"search_tools", "call_tool", "search", "get_schema", "execute"}
    messages = [r.message for r in caplog.records]
    assert any(f"Tool Search requested with profile='{profile}'" in m for m in messages)
    assert any(f"Code Mode requested with profile='{profile}'" in m for m in messages)


def test_discovery_modes_mutually_exclusive() -> None:
    """Mutual exclusion is checked before profile handling."""
    with pytest.raises(ValueError, match="mutually exclusive"):
        create_server(profile="gameday", enable_tool_search=True, enable_code_mode=True)
    with pytest.raises(ValueError, match="mutually exclusive"):
        create_server(profile="full", enable_tool_search=True, enable_code_mode=True)


@pytest.mark.asyncio
async def test_regex_tool_search_finds_and_calls_tools() -> None:
    """Regex Tool Search on full: search_tools finds a tool and call_tool runs it."""
    app = create_server(profile="full", enable_tool_search=True, tool_search_backend="regex")
    assert [t.name for t in await app.list_tools()] == ["search_tools", "call_tool"]
    found = await app.call_tool("search_tools", {"pattern": "scoreboard"})
    assert "games_get_scoreboard" in str(found.content)
    called = await app.call_tool(
        "call_tool", {"name": "games_get_scoreboard", "arguments": SCOREBOARD_ARGS}
    )
    assert not called.is_error
    assert "401816789" in str(called.content)


@pytest.mark.asyncio
async def test_bm25_tool_search_backend() -> None:
    """tool_search_backend='bm25' lists the same meta-tools and ranks by natural language."""
    app = create_server(profile="full", enable_tool_search=True, tool_search_backend="bm25")
    assert [t.name for t in await app.list_tools()] == ["search_tools", "call_tool"]
    found = await app.call_tool("search_tools", {"query": "betting odds point spreads"})
    assert not found.is_error
    assert "games_get_event_odds" in str(found.content)


@pytest.mark.asyncio
async def test_tool_search_backend_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """ESPN_MCP_TOOL_SEARCH_BACKEND=bm25 selects BM25 when the argument is omitted."""
    monkeypatch.setattr(settings, "MCP_TOOL_SEARCH_BACKEND", "bm25")
    monkeypatch.setattr(settings, "MCP_ENABLE_TOOL_SEARCH", True)
    app = create_server(profile="full")
    found = await app.call_tool("search_tools", {"query": "injury news headlines"})
    assert "news_get_news" in str(found.content)


@pytest.mark.asyncio
async def test_full_code_mode_attaches_when_available() -> None:
    """full + enable_code_mode lists the Code Mode meta-tools instead of the catalog."""
    app = create_server(profile="full", enable_code_mode=True)
    names = [t.name for t in await app.list_tools()]
    assert names == ["search", "get_schema", "execute"]


@pytest.mark.asyncio
async def test_full_code_mode_execute_runs_tool_chain() -> None:
    """execute runs Python in the Code Mode sandbox and reaches a real catalog tool."""
    app = create_server(profile="full", enable_code_mode=True)
    code = (
        "res = await call_tool('teams_list_teams', {'sport': 'football', 'league': 'nfl'})\n"
        "return str(res)"
    )
    async with Client(app) as client:
        res = await client.call_tool("execute", {"code": code}, raise_on_error=False)
    assert not res.is_error, res.content
    assert "Los Angeles Rams" in str(res.content)


@pytest.mark.asyncio
async def test_code_mode_skips_attach_without_sandbox(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Without pydantic-monty, Code Mode is skipped with a warning and the flat catalog stays."""
    monkeypatch.setattr(server.importlib.util, "find_spec", lambda *_a, **_k: None)
    with caplog.at_level(logging.WARNING):
        app = create_server(profile="full", enable_code_mode=True)
    names = await _tool_names(app)
    assert "execute" not in names
    assert len(names) == 33
    assert any("pydantic-monty" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_code_mode_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """ESPN_MCP_ENABLE_CODE_MODE=1 attaches Code Mode on full when the argument is omitted."""
    monkeypatch.setattr(settings, "MCP_ENABLE_CODE_MODE", True)
    assert "execute" in await _tool_names(create_server(profile="full"))


@pytest.mark.asyncio
async def test_code_mode_skips_attach_on_import_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """When CodeMode cannot be imported, create_server logs and keeps the flat catalog."""
    real_import = builtins.__import__

    def fake_import(
        name: str,
        globals: Any = None,
        locals: Any = None,
        fromlist: Any = (),
        level: int = 0,
    ) -> Any:
        if name == "fastmcp.experimental.transforms.code_mode":
            raise ImportError("simulated missing CodeMode")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with caplog.at_level(logging.WARNING):
        app = create_server(profile="full", enable_code_mode=True)
    names = await _tool_names(app)
    assert "execute" not in names
    assert len(names) == 33
    assert any("Code Mode requested but" in r.message for r in caplog.records)


@pytest.mark.parametrize(
    "argv",
    [
        ["--enable-tool-search", "--tool-search-backend", "bm25"],
        ["--enable-code-mode"],
    ],
)
def test_main_discovery_flags(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
    """CLI discovery flags reach create_server."""
    seen: dict[str, Any] = {}
    real_create = server.create_server

    def spy(**kwargs: Any) -> FastMCP:
        seen.update(kwargs)
        return real_create(**kwargs)

    monkeypatch.setattr(server, "create_server", spy)
    monkeypatch.setattr(FastMCP, "run", MagicMock())
    monkeypatch.setattr("sys.argv", ["espn-mcp", *argv])
    server.main()
    if "--enable-code-mode" in argv:
        assert seen["enable_code_mode"] is True
        assert seen["enable_tool_search"] is False
    else:
        assert seen["enable_tool_search"] is True
        assert seen["tool_search_backend"] == "bm25"


# ---------------------------------------------------------------------------
# Read-only: readOnlyHint is the single source of truth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_readonly_profile_equals_read_only_annotated_set() -> None:
    """The readonly profile exposes exactly the full tools annotated readOnlyHint=True."""
    annotated = await _read_only_names(create_server(profile="full"))
    assert await _tool_names(create_server(profile="readonly")) == annotated
    assert len(annotated) == 33


@pytest.mark.asyncio
async def test_readonly_profile_allows_reads() -> None:
    """profile=readonly enforces the gate itself; ESPN reads are annotated and pass."""
    app = create_server(profile="readonly")
    res = await app.call_tool("games_get_scoreboard", SCOREBOARD_ARGS)
    assert not res.is_error


def _annotation_probe_server(*, search: bool = True) -> FastMCP:
    """Minimal server with one tool per annotation state, gate on, and Tool Search."""
    app = FastMCP("annotation-probe")
    app.add_middleware(ReadOnlyGateMiddleware())

    @app.tool(annotations=ToolAnnotations(read_only_hint=True))
    def annotated_read() -> str:
        return "read"

    @app.tool(annotations=ToolAnnotations(read_only_hint=False))
    def annotated_write() -> str:
        return "write"

    @app.tool
    def unannotated() -> str:
        return "unannotated"

    @app.tool(annotations=ToolAnnotations(title="no hint"))
    def hint_missing() -> str:
        return "hint missing"

    if search:
        app.add_transform(RegexSearchTransform())
    return app


@pytest.mark.asyncio
async def test_gate_allows_read_only_annotation_directly_and_via_call_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """readOnlyHint=True is allowed under readonly, directly and through call_tool."""
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    app = _annotation_probe_server()
    direct = await app.call_tool("annotated_read", {})
    assert "read" in str(direct.content)
    proxied = await app.call_tool("call_tool", {"name": "annotated_read", "arguments": {}})
    assert "read" in str(proxied.content)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["annotated_write", "unannotated", "hint_missing"])
async def test_gate_refuses_false_or_missing_annotation(
    monkeypatch: pytest.MonkeyPatch, tool_name: str
) -> None:
    """readOnlyHint=False or no hint is a write: refused directly and via call_tool."""
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    app = _annotation_probe_server()
    with pytest.raises(SafetyViolationError, match=tool_name):
        await app.call_tool(tool_name, {})
    with pytest.raises(SafetyViolationError, match=tool_name):
        await app.call_tool("call_tool", {"name": tool_name, "arguments": {}})
    async with Client(app) as client:
        res = await client.call_tool(tool_name, {}, raise_on_error=False)
    assert res.is_error
    assert "read-only mode" in str(res.content)


@pytest.mark.asyncio
async def test_readonly_tool_filter_get_tool() -> None:
    """ReadOnlyToolFilter.get_tool returns read-only tools and drops the rest."""
    app = _annotation_probe_server()
    app.add_transform(ReadOnlyToolFilter())
    assert await app.get_tool("annotated_read") is not None
    assert await app.get_tool("annotated_write") is None
    assert await app.get_tool("unannotated") is None


@pytest.mark.asyncio
async def test_hidden_write_refused_unknown_name_is_unknown_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real write hidden by the filter is refused (catalog); an unknown name is Unknown tool."""
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    app = _annotation_probe_server(search=False)
    gate = next(m for m in app.middleware if isinstance(m, ReadOnlyGateMiddleware))
    gate.catalog = frozenset(await _tool_names(app))
    app.add_transform(ReadOnlyToolFilter())
    assert await _tool_names(app) == {"annotated_read"}
    with pytest.raises(SafetyViolationError, match="annotated_write"):
        await app.call_tool("annotated_write", {})
    with pytest.raises(NotFoundError, match="Unknown tool: 'not_a_tool'"):
        await app.call_tool("not_a_tool", {})


# ---------------------------------------------------------------------------
# Read-only through the discovery proxies on full
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_search_readonly_read_succeeds_on_wire(monkeypatch: pytest.MonkeyPatch) -> None:
    """On full + Tool Search + readonly: a read via call_tool works over the client."""
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    app = create_server(profile="full", enable_tool_search=True)
    async with Client(app) as client:
        read = await client.call_tool(
            "call_tool",
            {"name": "games_get_scoreboard", "arguments": SCOREBOARD_ARGS},
            raise_on_error=False,
        )
    assert not read.is_error
    assert "401816789" in str(read.content)


@pytest.mark.asyncio
async def test_gate_refuses_on_outer_call_tool_invocation(monkeypatch: pytest.MonkeyPatch) -> None:
    """The refusal comes from the gate unwrapping the outer call_tool, not an inner re-run."""
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    seen: list[tuple[str, str]] = []
    real_resolve = middleware._resolve_effective_tool_name

    def spy(context: MiddlewareContext) -> str:
        effective = real_resolve(context)
        seen.append((context.message.name, effective))
        return effective

    monkeypatch.setattr(middleware, "_resolve_effective_tool_name", spy)
    app = _annotation_probe_server()
    with pytest.raises(SafetyViolationError, match="annotated_write"):
        await app.call_tool("call_tool", {"name": "annotated_write", "arguments": {}})
    # Exactly one gate decision: on the outer proxy call. The inner call never ran.
    assert seen == [("call_tool", "annotated_write")]


@pytest.mark.asyncio
async def test_without_unwrap_the_proxy_gate_behaviour_breaks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard: with the unwrap removed, reads via call_tool are refused."""
    monkeypatch.setattr(settings, "MCP_READONLY", True)

    def no_unwrap(context: MiddlewareContext) -> str:
        name: Any = getattr(context.message, "name", "")
        return str(name)

    monkeypatch.setattr(middleware, "_resolve_effective_tool_name", no_unwrap)
    app = create_server(profile="full", enable_tool_search=True)
    with pytest.raises(SafetyViolationError, match="'call_tool' blocked"):
        await app.call_tool(
            "call_tool", {"name": "games_get_scoreboard", "arguments": SCOREBOARD_ARGS}
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [None, {"name": 123}, {"name": ""}])
async def test_call_tool_without_nested_name_fails_closed(
    monkeypatch: pytest.MonkeyPatch, arguments: Any
) -> None:
    """call_tool without a usable nested name is judged as itself: unannotated, refused."""
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    serving = SimpleNamespace(fastmcp=create_server(profile="full", enable_tool_search=True))

    async def allowed(_ctx: MiddlewareContext) -> str:
        return "allowed"

    ctx = MiddlewareContext(
        method="tools/call",
        message=SimpleNamespace(name="call_tool", arguments=arguments),
        fastmcp_context=serving,  # type: ignore[arg-type]
    )
    with pytest.raises(SafetyViolationError, match="'call_tool' blocked"):
        await ReadOnlyGateMiddleware().on_message(ctx, allowed)


@pytest.mark.asyncio
async def test_gate_without_server_context_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no serving FastMCP context the annotation cannot be read, so the call is refused."""
    monkeypatch.setattr(settings, "MCP_READONLY", True)

    async def allowed(_ctx: MiddlewareContext) -> str:
        return "allowed"

    ctx = MiddlewareContext(
        method="tools/call",
        message=SimpleNamespace(name="games_get_scoreboard", arguments={}),
    )
    with pytest.raises(SafetyViolationError, match="games_get_scoreboard"):
        await ReadOnlyGateMiddleware().on_message(ctx, allowed)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["regex", "bm25"])
async def test_search_tools_annotated_read_only_and_usable_under_readonly(
    monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    """search_tools carries readOnlyHint=True and works under readonly; call_tool does not."""
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    app = create_server(profile="full", enable_tool_search=True, tool_search_backend=backend)  # type: ignore[arg-type]
    listed = {t.name: t for t in await app.list_tools()}
    assert is_read_only_tool(listed["search_tools"])
    assert not is_read_only_tool(listed["call_tool"])
    query = {"pattern": "news"} if backend == "regex" else {"query": "news headlines"}
    res = await app.call_tool("search_tools", query)
    assert not res.is_error
    assert "news_get_news" in str(res.content)


@pytest.mark.asyncio
async def test_code_mode_discovery_read_only_and_execute_refused_under_readonly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Code Mode search/get_schema are annotated read-only; execute is refused under readonly."""
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    app = create_server(profile="full", enable_code_mode=True)
    listed = {t.name: t for t in await app.list_tools()}
    assert is_read_only_tool(listed["search"])
    assert is_read_only_tool(listed["get_schema"])
    assert not is_read_only_tool(listed["execute"])
    with pytest.raises(SafetyViolationError, match="'execute' blocked"):
        await app.call_tool("execute", {"code": "return 1"})


# ---------------------------------------------------------------------------
# Read-only: unknown names keep FastMCP's "Unknown tool"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_readonly_unknown_name_direct_is_unknown_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct call to a name outside the catalog gets Unknown tool, not the read-only refusal."""
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    app = create_server(profile="full")
    with pytest.raises(NotFoundError, match="Unknown tool: 'espn_get_scoreboard'"):
        await app.call_tool("espn_get_scoreboard", {})
    async with Client(app) as client:
        res = await client.call_tool("espn_get_scoreboard", {}, raise_on_error=False)
    assert res.is_error
    assert "Unknown tool" in str(res.content)
    assert "read-only" not in str(res.content)


@pytest.mark.asyncio
async def test_readonly_unknown_name_via_call_tool_is_unknown_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """call_tool with a name outside the catalog gets Unknown tool on full + search."""
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    app = create_server(profile="full", enable_tool_search=True)
    with pytest.raises(ToolError, match="Unknown tool: 'games_nope'") as exc_info:
        await app.call_tool("call_tool", {"name": "games_nope", "arguments": {}})
    assert not isinstance(exc_info.value, SafetyViolationError)


@pytest.mark.asyncio
async def test_readonly_outside_allowlist_is_unknown_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """Under read-only, a tool outside the active allowlist profile is unknown, like without it."""
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    app = create_server(profile="gameday")
    with pytest.raises(NotFoundError, match="Unknown tool"):
        await app.call_tool("teams_get_athlete_bio", {})
    res = await app.call_tool("games_get_scoreboard", SCOREBOARD_ARGS)
    assert not res.is_error


@pytest.mark.asyncio
async def test_readonly_switched_on_after_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """With read-only switched on at runtime (no filter, empty gate catalog), unknown names
    still reach Unknown tool and reads still run."""
    app = create_server(profile="full")
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    with pytest.raises(NotFoundError, match="Unknown tool"):
        await app.call_tool("games_nope", {})
    res = await app.call_tool("games_get_scoreboard", SCOREBOARD_ARGS)
    assert not res.is_error


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["full", "gameday", "readonly"])
async def test_readonly_call_tool_without_search_is_unknown_tool(
    monkeypatch: pytest.MonkeyPatch, profile: str
) -> None:
    """Without Tool Search, call_tool is not on the server: Unknown tool 'call_tool'."""
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    app = create_server(profile=profile, enable_tool_search=False)
    arguments = {"name": "games_get_scoreboard", "arguments": SCOREBOARD_ARGS}
    with pytest.raises(NotFoundError, match="Unknown tool: 'call_tool'") as exc_info:
        await app.call_tool("call_tool", arguments)
    assert not isinstance(exc_info.value, SafetyViolationError)
