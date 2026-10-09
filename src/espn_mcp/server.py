"""FastMCP root gateway, Server Composition, hierarchical middleware, and entrypoint.

Conforms to MCP 2026-07-28 specifications.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import logging
import signal
import sys
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastmcp import FastMCP
from fastmcp.experimental.transforms.code_mode import CodeMode
from fastmcp.server.transforms.search import BM25SearchTransform, RegexSearchTransform
from fastmcp.tools import Tool
from mcp.server.caching import CacheHint

from espn_mcp import __version__
from espn_mcp.client import ESPNClient as ESPNClient
from espn_mcp.client import default_client
from espn_mcp.config import settings
from espn_mcp.middleware import ParentAuditMiddleware, ReadOnlyGateMiddleware
from espn_mcp.profiles import (
    FULL_ONLY_TOOLS,
    PROFILES,
    ReadOnlyAnnotations,
    ReadOnlyToolFilter,
    get_profile,
    validate_allowlist,
)
from espn_mcp.tools import (
    game_analysis_prompt,
    games_server,
    get_athlete_bio,
    get_athlete_gamelog,
    get_athlete_overview,
    get_athlete_splits,
    get_athlete_stats,
    get_calendar,
    get_capabilities,
    get_event_odds,
    get_futures,
    get_game_predictor,
    get_game_situation,
    get_game_summary,
    get_leaders_by_athlete,
    get_leaders_by_team,
    get_league_draft,
    get_league_events,
    get_league_groups,
    get_news,
    get_play_by_play,
    get_player_stats,
    get_power_index,
    get_rankings,
    get_scoreboard,
    get_scoreboard_header,
    get_standings,
    get_supported_leagues,
    get_team,
    get_team_depth_chart,
    get_team_roster,
    get_team_schedule,
    get_team_statistics,
    get_transactions,
    get_win_probabilities,
    list_teams,
    news_server,
    search,
    team_evaluation_prompt,
    teams_server,
)

CacheableMethod = Literal[
    "prompts/list",
    "resources/list",
    "resources/read",
    "resources/templates/list",
    "server/discover",
    "tools/list",
]

ToolSearchBackend = Literal["regex", "bm25"]

logger = logging.getLogger(__name__)

# Domain sub-servers by mount namespace (hybrid A+B: the namespace is the domain).
DOMAIN_SERVERS: dict[str, FastMCP] = {
    "games": games_server,
    "teams": teams_server,
    "news": news_server,
}

# FastMCP synthetic discovery tools that only read the catalog (annotated readOnlyHint=True).
TOOL_SEARCH_READ_ONLY_TOOLS = ("search_tools",)
CODE_MODE_READ_ONLY_TOOLS = ("search", "get_schema")

# MCP 2026-07-28 Deterministic Caching Hints (SEP-2549)
CACHE_HINTS: dict[CacheableMethod, CacheHint] = {
    "tools/list": CacheHint(ttl_ms=settings.CATALOG_CACHE_TTL_MS, scope="public"),
    "prompts/list": CacheHint(ttl_ms=settings.CATALOG_CACHE_TTL_MS, scope="public"),
    "resources/list": CacheHint(ttl_ms=settings.CATALOG_CACHE_TTL_MS, scope="public"),
    "resources/templates/list": CacheHint(ttl_ms=settings.CATALOG_CACHE_TTL_MS, scope="public"),
    "server/discover": CacheHint(ttl_ms=settings.CATALOG_CACHE_TTL_MS, scope="public"),
}

client = default_client


@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Manage server lifecycle and persistent client resources."""
    logger.info("Starting up ESPN MCP server")
    try:
        yield {"client": client}
    finally:
        logger.info("Shutting down ESPN MCP resources")
        await client.close()


def _streamable_http_app(
    self: FastMCP,
    path: str | None = None,
    stateless_http: bool | None = None,
    json_response: bool | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
    **kwargs: Any,
) -> Any:
    """Compatibility bridge for streamable HTTP ASGI application."""
    allowed_hosts = kwargs.pop("allowed_hosts", None)
    if allowed_hosts is None:
        allowed_hosts = [host, "localhost", f"{host}:{port}", f"localhost:{port}"]
    return self.http_app(
        path=path,
        transport="streamable-http",
        stateless_http=stateless_http,
        json_response=json_response,
        host_origin_protection=True,
        allowed_hosts=allowed_hosts,
        **kwargs,
    )


# Ensure Tool instances expose input_schema property
if not hasattr(Tool, "input_schema"):
    Tool.input_schema = property(lambda self: getattr(self, "parameters", {}))  # type: ignore[attr-defined]


def _catalog_tool_names(root: FastMCP) -> set[str]:
    """Return the client-visible tool names of ``root`` via the public ``list_tools()``.

    Runs on a worker thread with its own event loop so ``create_server`` stays synchronous
    and safe to call from inside a running loop (tests, hosts).
    """

    async def _collect() -> set[str]:
        return {tool.name for tool in await root.list_tools()}

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, _collect()).result()


def _apply_tool_allowlist(root: FastMCP, allowlist: frozenset[str]) -> None:
    """Expose only ``allowlist`` tools; prompts, resources, and templates are untouched.

    Public visibility API: disable every tool, then re-enable the named tools. The later
    ``enable`` wins. ``enable(only=True)`` is not used because it disables every component
    type first, which would also hide prompts and resources.
    """
    root.disable(components={"tool"})
    root.enable(names=set(allowlist), components={"tool"})


def _attach_tool_search(root: FastMCP, backend: ToolSearchBackend) -> None:
    """Attach Regex (default) or BM25 Tool Search transform to the root gateway."""
    if backend == "bm25":
        root.add_transform(BM25SearchTransform())
    else:
        root.add_transform(RegexSearchTransform())
    root.add_transform(ReadOnlyAnnotations(TOOL_SEARCH_READ_ONLY_TOOLS))


def _code_mode_sandbox_available() -> bool:
    """True when ``pydantic_monty`` (shipped by ``fastmcp[code-mode]``) is importable.

    Code Mode imports without it, but every ``execute`` call then fails, so attach is skipped.
    """
    return importlib.util.find_spec("pydantic_monty") is not None


def _attach_code_mode(root: FastMCP) -> bool:
    """Attach experimental Code Mode when its sandbox is installed.

    ``fastmcp>=4.0.11`` always ships ``CodeMode``; only the ``pydantic_monty`` sandbox is
    optional. Returns True when the transform was attached; False when the sandbox is missing.
    """
    if not _code_mode_sandbox_available():
        logger.warning(
            "Code Mode requested but pydantic-monty (the Code Mode sandbox) is not installed; "
            "skipping attach. Install fastmcp[code-mode] or omit --enable-code-mode."
        )
        return False
    root.add_transform(CodeMode())
    root.add_transform(ReadOnlyAnnotations(CODE_MODE_READ_ONLY_TOOLS))
    return True


def create_server(
    profile: str | None = None,
    enable_tool_search: bool | None = None,
    enable_code_mode: bool | None = None,
    tool_search_backend: ToolSearchBackend | None = None,
) -> FastMCP:
    """Build the composed root FastMCP gateway.

    Architecture:
    Gateway (FastMCP)
    ├── Parent Middleware (ParentAuditMiddleware, ReadOnlyGateMiddleware)
    ├── mount(games_server, namespace="games")   # domain mount, not a product stamp
    ├── mount(teams_server, namespace="teams")
    ├── mount(news_server, namespace="news")
    ├── (allowlist profiles) tools-only visibility allowlist over the full catalog
    ├── (readonly) ReadOnlyToolFilter: keep tools annotated readOnlyHint=True
    └── (Optional, profile==full only) Tool Search XOR experimental Code Mode

    Profile rules:
    * Domain-mount profiles mount the listed domains; allowlist profiles mount every
      domain and expose only the allowlisted tool names (prompts/resources stay).
    * An unknown profile, or an allowlisted name missing from the full catalog, raises
      ``ValueError`` at build time.

    Discovery rules:
    * Default: flat ``tools/list`` of mounted domain tools (curated or full).
    * Tool Search / Code Mode attach only when explicitly enabled **and** ``profile == "full"``.
    * Requesting either discovery mode on a curated profile logs a warning and skips attach.
    * Enabling both Tool Search and Code Mode raises ``ValueError`` (mutual exclusion).
    """
    active = get_profile(profile or settings.MCP_PROFILE)
    active_profile = active.name
    use_tool_search = (
        enable_tool_search if enable_tool_search is not None else settings.MCP_ENABLE_TOOL_SEARCH
    )
    use_code_mode = (
        enable_code_mode if enable_code_mode is not None else settings.MCP_ENABLE_CODE_MODE
    )
    search_backend: ToolSearchBackend = (
        tool_search_backend if tool_search_backend is not None else settings.MCP_TOOL_SEARCH_BACKEND
    )

    if use_tool_search and use_code_mode:
        raise ValueError(
            "Tool Search and Code Mode are mutually exclusive; enable only one discovery mode."
        )

    root = FastMCP(
        "espn-mcp",
        version=__version__,
        lifespan=server_lifespan,
        cache_ttl=settings.CATALOG_CACHE_TTL_MS // 1000,
        cache_scope="public",
    )

    # Compatibility bridge
    root.streamable_http_app = _streamable_http_app.__get__(root, FastMCP)  # type: ignore[attr-defined]

    # 1. Global Parent Middleware (Audit logging, request timing, and read-only gate)
    readonly_gate = ReadOnlyGateMiddleware(enforce=active.readonly)
    root.add_middleware(ParentAuditMiddleware())
    root.add_middleware(readonly_gate)

    # 2. Server Composition via mount(subserver, namespace=...) — Hybrid A+B domain mounts
    for domain in active.domains:
        root.mount(DOMAIN_SERVERS[domain], namespace=domain)

    # 3. Allowlist profiles: validate against the full mounted catalog, then filter tools
    if active.is_allowlist:
        allowlist = validate_allowlist(active, _catalog_tool_names(root))
        _apply_tool_allowlist(root, allowlist)

    # 4. Read-only: keep only tools annotated readOnlyHint=True (annotation is the truth)
    if active.readonly or settings.MCP_READONLY:
        # Capture the profile catalog before the filter hides writes, so the gate refuses a
        # hidden real tool but lets an unknown name reach FastMCP's "Unknown tool" error.
        readonly_gate.catalog = frozenset(_catalog_tool_names(root))
        root.add_transform(ReadOnlyToolFilter())

    # 5. Opt-in discovery transforms — full profile only (never dump full catalog by default)
    if use_tool_search:
        if active_profile != "full":
            logger.warning(
                "Tool Search requested with profile=%r; attach is allowed only on profile='full'. "
                "Keeping flat curated tools/list.",
                active_profile,
            )
        else:
            _attach_tool_search(root, search_backend)

    if use_code_mode:
        if active_profile != "full":
            logger.warning(
                "Code Mode requested with profile=%r; attach is allowed only on profile='full'. "
                "Keeping flat curated tools/list.",
                active_profile,
            )
        else:
            _attach_code_mode(root)

    return root


# Default server instance
mcp = create_server()


def _handle_shutdown(signum: int, frame: Any) -> None:
    """Handle SIGTERM/SIGINT from host supervisor and unwind gracefully."""
    logger.info("Received signal %s; shutting down.", signum)
    sys.exit(0)


def main() -> None:
    """Run MCPServer with transport selection and graceful shutdown handling."""
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    parser = argparse.ArgumentParser(description="ESPN MCP Server (2026-07-28 Spec)")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http", "sse"],
        default="stdio",
        help="Transport protocol: 'stdio' (default), 'streamable-http' (modern), or 'sse'.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host address for HTTP transports (default: 127.0.0.1).",
    )
    parser.add_argument("--port", type=int, default=8000, help="Port for HTTP transports.")
    parser.add_argument(
        "--profile",
        type=str.lower,
        choices=sorted(PROFILES),
        default=settings.MCP_PROFILE.lower(),
        help=(
            "Server profile (default 'full'): domain-mount profiles full/games/teams/news/readonly "
            "or job-shaped allowlist profiles gameday/betting/scouting/season."
        ),
    )
    parser.add_argument(
        "--enable-tool-search",
        action="store_true",
        default=settings.MCP_ENABLE_TOOL_SEARCH,
        help=(
            "Enable Tool Search on profile=full only "
            "(replaces tools/list with search_tools + call_tool)."
        ),
    )
    parser.add_argument(
        "--tool-search-backend",
        choices=["regex", "bm25"],
        default=settings.MCP_TOOL_SEARCH_BACKEND,
        help="Tool Search backend: 'regex' (default) or 'bm25'.",
    )
    parser.add_argument(
        "--enable-code-mode",
        action="store_true",
        default=settings.MCP_ENABLE_CODE_MODE,
        help=(
            "Enable experimental Code Mode on profile=full only "
            "(search + execute). Mutually exclusive with --enable-tool-search."
        ),
    )
    parser.add_argument(
        "--stateless",
        action=argparse.BooleanOptionalAction,
        default=settings.MCP_STATELESS_HTTP,
        help=(
            "Run Streamable HTTP in stateless mode "
            "(fresh connection per request, no Mcp-Session-Id)."
        ),
    )
    parser.add_argument(
        "--json-response",
        action=argparse.BooleanOptionalAction,
        default=settings.MCP_JSON_RESPONSE,
        help="Return direct JSON responses instead of SSE text/event-stream over Streamable HTTP.",
    )
    parser.add_argument(
        "--allowed-host",
        action="append",
        dest="allowed_hosts",
        default=None,
        help="Allowed host for HTTP transports (can be specified multiple times).",
    )
    parser.add_argument(
        "--allowed-origin",
        action="append",
        dest="allowed_origins",
        default=None,
        help="Allowed origin for HTTP transports (can be specified multiple times).",
    )
    args = parser.parse_args()

    # Build the server for the requested profile and discovery flags
    server_instance = create_server(
        profile=args.profile,
        enable_tool_search=args.enable_tool_search,
        enable_code_mode=args.enable_code_mode,
        tool_search_backend=args.tool_search_backend,
    )

    if args.transport != "streamable-http":
        if args.stateless:
            logger.warning("--stateless flag is only applicable to 'streamable-http' transport.")
        if args.json_response:
            logger.warning(
                "--json-response flag is only applicable to 'streamable-http' transport."
            )

    hosts = getattr(args, "allowed_hosts", None)
    if hosts is None:
        if args.transport == "streamable-http" and args.host in ("0.0.0.0", "::"):
            parser.error("--allowed-host is required when binding to a wildcard host")
        hosts = [args.host, "localhost", f"{args.host}:{args.port}", f"localhost:{args.port}"]
    elif any(h.strip() == "*" for h in hosts):
        parser.error("Wildcard '*' is not permitted in --allowed-host; specify explicit hostnames.")
    run_kwargs: dict[str, Any] = {
        "host": args.host,
        "port": args.port,
        "host_origin_protection": True,
        "allowed_hosts": hosts,
    }
    if args.allowed_origins is not None:
        run_kwargs["allowed_origins"] = args.allowed_origins

    if args.transport == "sse":
        logger.warning(
            "Deprecation Warning: HTTP+SSE transport is deprecated per MCP 2026-07-28 spec "
            "(SEP-2577). Please migrate to Streamable HTTP (--transport streamable-http)."
        )
        server_instance.run(
            transport="sse",
            **run_kwargs,
        )
    elif args.transport == "streamable-http":
        server_instance.run(
            transport="streamable-http",
            stateless_http=args.stateless,
            json_response=args.json_response,
            **run_kwargs,
        )
    else:
        server_instance.run(transport="stdio")


if __name__ == "__main__":
    main()

# Backward-compatible re-exports
__all__ = [
    "CACHE_HINTS",
    "DOMAIN_SERVERS",
    "FULL_ONLY_TOOLS",
    "PROFILES",
    "create_server",
    "game_analysis_prompt",
    "get_athlete_bio",
    "get_athlete_gamelog",
    "get_athlete_overview",
    "get_athlete_splits",
    "get_athlete_stats",
    "get_calendar",
    "get_capabilities",
    "get_event_odds",
    "get_futures",
    "get_game_predictor",
    "get_game_situation",
    "get_game_summary",
    "get_leaders_by_athlete",
    "get_leaders_by_team",
    "get_league_draft",
    "get_league_events",
    "get_league_groups",
    "get_news",
    "get_play_by_play",
    "get_player_stats",
    "get_power_index",
    "get_rankings",
    "get_scoreboard",
    "get_scoreboard_header",
    "get_standings",
    "get_supported_leagues",
    "get_team",
    "get_team_depth_chart",
    "get_team_roster",
    "get_team_schedule",
    "get_team_statistics",
    "get_transactions",
    "get_win_probabilities",
    "list_teams",
    "main",
    "mcp",
    "search",
    "server_lifespan",
    "team_evaluation_prompt",
]
