"""Hierarchical middleware for FastMCP 4 Server Composition.

Provides:
- ParentAuditMiddleware: Gateway-level execution timing, audit logging, and secret scrubbing.
- ReadOnlyGateMiddleware: Gateway-level read-only enforcement when ESPN_MCP_READONLY=1.
- GamesDomainGuardMiddleware: Child domain guardrail validating query limits.
- TeamsDomainGuardMiddleware: Child domain guardrail validating team/athlete identifiers.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext

from espn_mcp.config import settings
from espn_mcp.errors import redact_secrets

logger = logging.getLogger(__name__)


class ParentAuditMiddleware(Middleware):
    """Global gateway middleware logging operation timing, method, and sanitizing errors."""

    async def on_message(
        self,
        context: MiddlewareContext,
        call_next: Callable[[MiddlewareContext], Any],
    ) -> Any:
        """Intercept request, track timing, log lifecycle, and redact sensitive output."""
        start_time = time.perf_counter()
        method = getattr(context, "method", "unknown")
        tool_name = getattr(context.message, "name", None) if context.message else None
        target = f"{method}:{tool_name}" if tool_name else method

        logger.debug("→ MCP Request received: %s", target)
        try:
            result = await call_next(context)
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            logger.debug("← MCP Request completed: %s in %.2fms", target, duration_ms)
            return result
        except Exception as exc:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            sanitized_msg = redact_secrets(str(exc))
            logger.error(
                "✗ MCP Request failed: %s in %.2fms: %s", target, duration_ms, sanitized_msg
            )
            raise


class ReadOnlyGateMiddleware(Middleware):
    """Gateway-level middleware enforcing read-only operation when ESPN_MCP_READONLY=1."""

    MUTATING_PREFIXES = ("create_", "delete_", "bulk_delete_", "update_", "drop_", "truncate_")

    async def on_message(
        self,
        context: MiddlewareContext,
        call_next: Callable[[MiddlewareContext], Any],
    ) -> Any:
        """Reject mutating operations when server runs in read-only mode."""
        if settings.MCP_READONLY and getattr(context, "method", None) == "tools/call":
            tool_name = getattr(context.message, "name", "") if context.message else ""
            base_name = tool_name.split("_", 1)[1] if "_" in tool_name else tool_name
            if any(base_name.startswith(p) for p in self.MUTATING_PREFIXES):
                logger.warning("Blocked mutating tool call in read-only mode: %s", tool_name)
                raise PermissionError(
                    f"Server is in read-only mode (ESPN_MCP_READONLY=1). "
                    f"Tool '{tool_name}' is blocked."
                )
        return await call_next(context)


class GamesDomainGuardMiddleware(Middleware):
    """Child domain guard middleware for the games sub-server."""

    async def on_message(
        self,
        context: MiddlewareContext,
        call_next: Callable[[MiddlewareContext], Any],
    ) -> Any:
        """Validate game domain arguments and enforce query bounds."""
        if getattr(context, "method", None) == "tools/call":
            arguments = getattr(context.message, "arguments", {}) or {}
            limit = arguments.get("limit")
            if limit is not None and isinstance(limit, int) and limit > 100:
                raise ValueError(
                    f"Scoreboard limit {limit} exceeds maximum allowable batch size of 100."
                )
        return await call_next(context)


class TeamsDomainGuardMiddleware(Middleware):
    """Child domain guard middleware for the teams/roster sub-server."""

    async def on_message(
        self,
        context: MiddlewareContext,
        call_next: Callable[[MiddlewareContext], Any],
    ) -> Any:
        """Validate team and athlete identifiers."""
        if getattr(context, "method", None) == "tools/call":
            arguments = getattr(context.message, "arguments", {}) or {}
            team_id = arguments.get("team_id")
            if team_id is not None and str(team_id).strip() == "":
                raise ValueError("team_id cannot be empty or whitespace.")
            athlete_id = arguments.get("athlete_id")
            if athlete_id is not None and str(athlete_id).strip() == "":
                raise ValueError("athlete_id cannot be empty or whitespace.")
        return await call_next(context)
