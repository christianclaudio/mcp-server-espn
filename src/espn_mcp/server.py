"""FastMCP root gateway, Server Composition, hierarchical middleware, and entrypoint.

Conforms to MCP 2026-07-28 specifications.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastmcp import FastMCP
from fastmcp.tools import Tool
from mcp.server.caching import CacheHint

from espn_mcp import __version__
from espn_mcp.client import ESPNClient as ESPNClient
from espn_mcp.client import default_client
from espn_mcp.config import settings
from espn_mcp.middleware import ParentAuditMiddleware, ReadOnlyGateMiddleware
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

logger = logging.getLogger(__name__)

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


def create_server(
    profile: str | None = None,
    enable_tool_search: bool | None = None,
) -> FastMCP:
    """Factory creating the composed root FastMCP gateway.

    Mounts domain sub-servers (games, teams, news) based on the requested profile.
    Applies parent-level middleware (ParentAuditMiddleware, ReadOnlyGateMiddleware).
    Optionally applies RegexSearchTransform when tool search is enabled.
    """
    active_profile = (profile or settings.MCP_PROFILE).lower()
    active_tool_search = (
        enable_tool_search if enable_tool_search is not None else settings.MCP_ENABLE_TOOL_SEARCH
    )

    root = FastMCP(
        "espn-mcp",
        version=__version__,
        lifespan=server_lifespan,
        cache_ttl=settings.CATALOG_CACHE_TTL_MS // 1000,
        cache_scope="public",
    )

    # 1. Global Parent Middleware (Audit logging, request timing, and read-only gate)
    root.add_middleware(ParentAuditMiddleware())
    root.add_middleware(ReadOnlyGateMiddleware())

    # 2. Server Composition via mount(subserver, namespace=...)
    if active_profile in ("full", "games", "readonly"):
        root.mount(games_server, namespace="games")
    if active_profile in ("full", "teams", "readonly"):
        root.mount(teams_server, namespace="teams")
    if active_profile in ("full", "news", "readonly"):
        root.mount(news_server, namespace="news")

    # 3. Dynamic tool search (opt-in; default preserves standard flat tools/list)
    if active_tool_search:
        from fastmcp.server.transforms.search import RegexSearchTransform

        root.add_transform(RegexSearchTransform())

    # Compatibility bridge
    root.streamable_http_app = _streamable_http_app.__get__(root, FastMCP)  # type: ignore[attr-defined]

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
        choices=["full", "games", "teams", "news", "readonly"],
        default=settings.MCP_PROFILE,
        help="Domain profile: 'full', 'games', 'teams', 'news', or 'readonly'.",
    )
    parser.add_argument(
        "--enable-tool-search",
        action="store_true",
        default=settings.MCP_ENABLE_TOOL_SEARCH,
        help="Enable dynamic tool search transform instead of flat tools/list.",
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

    # Re-instantiate server if custom profile or tool search specified
    server_instance = create_server(
        profile=args.profile,
        enable_tool_search=args.enable_tool_search,
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
