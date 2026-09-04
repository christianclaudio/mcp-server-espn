"""MCPServer instance, tool definitions, annotations, and entrypoint conforming to 2026-07-28."""

from __future__ import annotations

import argparse
import logging
import os
import signal
from typing import Any, Literal

from mcp.server import CacheHint, MCPServer
from mcp.server.mcpserver import Context
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from template_mcp.client import TemplateClient
from template_mcp.config import settings
from template_mcp.errors import SafetyViolationError, redact_secrets

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
    "tools/list": CacheHint(ttl_ms=3600000, scope="public"),
    "prompts/list": CacheHint(ttl_ms=3600000, scope="public"),
    "resources/list": CacheHint(ttl_ms=3600000, scope="public"),
    "server/discover": CacheHint(ttl_ms=3600000, scope="public"),
}

# Initialize server
mcp = MCPServer(
    "template-mcp",
    version="1.1.0",
    cache_hints=CACHE_HINTS,
)
client = TemplateClient()

# MCP Behavioral Annotations
ANNOTATION_READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)

ANNOTATION_MUTATION = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=False,
)

ANNOTATION_DESTRUCTIVE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=False,
    open_world_hint=False,
)


class ConfirmationResponse(BaseModel):
    """Schema for Multi Round-Trip Request (MRTR) user confirmation per SEP-2322."""

    confirm: bool = Field(description="Explicit confirmation to execute the requested operation.")


@mcp.tool(
    name="get_health_status",
    description="Check the connectivity and operational health of the upstream API.",
    annotations=ANNOTATION_READ_ONLY,
)
async def get_health_status() -> dict[str, Any]:
    """Verify connectivity to upstream service."""
    try:
        data = await client.request("GET", "health")
        return {"status": "success", "data": data}
    except Exception as exc:
        return {"status": "error", "message": redact_secrets(str(exc))}


@mcp.tool(
    name="list_items",
    description="List available resource items with optional pagination.",
    annotations=ANNOTATION_READ_ONLY,
)
async def list_items(limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """Query items from the upstream service."""
    try:
        params = {"limit": limit, "offset": offset}
        data = await client.request("GET", "items", params=params)
        return {"status": "success", "data": data}
    except Exception as exc:
        return {"status": "error", "message": redact_secrets(str(exc))}


@mcp.tool(
    name="get_item",
    description="Retrieve details for a specific item by ID.",
    annotations=ANNOTATION_READ_ONLY,
)
async def get_item(item_id: str) -> dict[str, Any]:
    """Fetch single item by sanitized ID."""
    try:
        sanitized_id = client.sanitize_path_param(item_id)
        data = await client.request("GET", f"items/{sanitized_id}")
        return {"status": "success", "data": data}
    except Exception as exc:
        return {"status": "error", "message": redact_secrets(str(exc))}


@mcp.tool(
    name="create_item",
    description="Create a new item with specified attributes.",
    annotations=ANNOTATION_MUTATION,
)
async def create_item(name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create a new resource item."""
    if settings.MCP_READONLY:
        return {"status": "error", "message": "Server running in read-only mode (MCP_READONLY=1)."}
    try:
        body = {"name": name, **(payload or {})}
        data = await client.request("POST", "items", json_data=body)
        return {"status": "success", "data": data}
    except Exception as exc:
        return {"status": "error", "message": redact_secrets(str(exc))}


@mcp.tool(
    name="delete_item",
    description="Delete a single resource item. Requires confirm=True or interactive confirmation.",
    annotations=ANNOTATION_DESTRUCTIVE,
)
async def delete_item(
    item_id: str,
    confirm: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Delete single item with mandatory confirmation gate and MRTR elicitation support."""
    if settings.MCP_READONLY:
        return {"status": "error", "message": "Server running in read-only mode (MCP_READONLY=1)."}

    # MRTR interactive elicitation if confirm is False and client context is available
    if not confirm and ctx is not None:
        try:
            elicit_res = await ctx.elicit(
                message=f"Are you sure you want to delete item '{item_id}'?",
                schema=ConfirmationResponse,
            )
            if elicit_res.action == "accept" and elicit_res.data and elicit_res.data.confirm:
                confirm = True
        except Exception:
            pass

    if not confirm:
        raise SafetyViolationError(
            f"Safety Gate: Deleting item '{item_id}' requires explicit confirm=True."
        )
    try:
        sanitized_id = client.sanitize_path_param(item_id)
        data = await client.request("DELETE", f"items/{sanitized_id}")
        return {"status": "success", "deleted": True, "data": data}
    except Exception as exc:
        return {"status": "error", "message": redact_secrets(str(exc))}


@mcp.tool(
    name="bulk_delete_items",
    description="Batch delete multiple items. Gated by TEMPLATE_MCP_ALLOW_BULK_DESTRUCTIVE.",
    annotations=ANNOTATION_DESTRUCTIVE,
)
async def bulk_delete_items(
    item_ids: list[str],
    dry_run: bool = True,
    confirm: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Bulk delete with dry-run default, environment gating, and MRTR confirmation."""
    if settings.MCP_READONLY:
        return {"status": "error", "message": "Server running in read-only mode."}
    if not settings.MCP_ALLOW_BULK_DESTRUCTIVE:
        raise SafetyViolationError(
            "Bulk destructive operations disabled. Set TEMPLATE_MCP_ALLOW_BULK_DESTRUCTIVE=1."
        )
    if dry_run:
        return {
            "status": "dry_run",
            "message": f"Simulated deletion of {len(item_ids)} items.",
            "item_ids": item_ids,
        }

    # MRTR interactive elicitation if live delete requested without confirm
    if not confirm and ctx is not None:
        try:
            elicit_res = await ctx.elicit(
                message=f"Are you sure you want to permanently bulk delete {len(item_ids)} items?",
                schema=ConfirmationResponse,
            )
            if elicit_res.action == "accept" and elicit_res.data and elicit_res.data.confirm:
                confirm = True
        except Exception:
            pass

    if not confirm:
        raise SafetyViolationError("Live bulk deletion requires explicit confirm=True.")

    try:
        results = []
        for i_id in item_ids:
            sanitized_id = client.sanitize_path_param(i_id)
            res = await client.request("DELETE", f"items/{sanitized_id}")
            results.append({"id": i_id, "result": res})
        return {"status": "success", "deleted_count": len(results), "results": results}
    except Exception as exc:
        return {"status": "error", "message": redact_secrets(str(exc))}


@mcp.resource("template://reference/capabilities")
def get_capabilities() -> str:
    """Return static system capabilities reference."""
    return "Template MCP Server capabilities: Health, Items, Bulk, MRTR Elicitations."


@mcp.prompt("analyze_item")
def analyze_item_prompt(item_id: str) -> str:
    """Generate prompt template for analyzing a resource."""
    return f"Please retrieve item '{item_id}' using get_item and analyze its configuration."


def _handle_shutdown(signum: int, frame: Any) -> None:
    """Gracefully handle SIGTERM/SIGINT from host supervisor to exit with status 0 immediately."""
    os._exit(0)


def main() -> None:
    """Run MCPServer with transport selection and graceful shutdown handling."""
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    parser = argparse.ArgumentParser(description="Template MCP Server (2026-07-28 Spec)")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http", "sse"],
        default="stdio",
        help="Transport protocol: 'stdio' (default), 'streamable-http' (modern), or 'sse'.",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Host address for HTTP transports.")
    parser.add_argument("--port", type=int, default=8000, help="Port for HTTP transports.")
    args = parser.parse_args()

    if args.transport == "sse":
        logger.warning(
            "Deprecation Warning: HTTP+SSE transport is deprecated per MCP 2026-07-28 spec "
            "(SEP-2577). Please migrate to Streamable HTTP (--transport streamable-http)."
        )
        mcp.run(transport="sse", host=args.host, port=args.port)
    elif args.transport == "streamable-http":
        mcp.run(transport="streamable-http", host=args.host, port=args.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
