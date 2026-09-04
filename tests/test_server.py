"""Tests for FastMCP server tools, resources, prompts, MRTR elicitation, and safety gates."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from template_mcp import server
from template_mcp.config import settings
from template_mcp.errors import SafetyViolationError


@pytest.mark.asyncio
async def test_server_tools(mock_transport, monkeypatch):
    async_client = httpx.AsyncClient(transport=mock_transport, base_url="https://api.example.com")
    monkeypatch.setattr(server, "client", server.TemplateClient(http_client=async_client))

    # 1. Health
    health = await server.get_health_status()
    assert health["status"] == "success"
    assert health["data"]["status"] == "healthy"

    # 2. List items
    items = await server.list_items(limit=10, offset=0)
    assert items["status"] == "success"

    # 3. Get item
    item = await server.get_item(item_id="123")
    assert item["status"] == "success"
    assert item["data"]["id"] == "123"

    # 4. Create item
    created = await server.create_item(name="New Item", payload={"description": "Test"})
    assert created["status"] == "success"
    assert created["data"]["created"] is True

    # 5. Delete item (confirm=False should raise SafetyViolationError)
    with pytest.raises(SafetyViolationError):
        await server.delete_item(item_id="123", confirm=False)

    # 6. Delete item (confirm=True)
    deleted = await server.delete_item(item_id="123", confirm=True)
    assert deleted["status"] == "success"
    assert deleted["deleted"] is True

    # 7. Bulk delete (default dry_run=True, but gated by settings)
    with pytest.raises(SafetyViolationError) as exc_info:
        await server.bulk_delete_items(item_ids=["1", "2"], dry_run=True)
    assert "TEMPLATE_MCP_ALLOW_BULK_DESTRUCTIVE=1" in str(exc_info.value)

    # Enable bulk destructive
    monkeypatch.setattr(settings, "MCP_ALLOW_BULK_DESTRUCTIVE", True)
    dry_run_res = await server.bulk_delete_items(item_ids=["1", "2"], dry_run=True)
    assert dry_run_res["status"] == "dry_run"

    # Bulk delete live without confirm
    with pytest.raises(SafetyViolationError):
        await server.bulk_delete_items(item_ids=["1", "2"], dry_run=False, confirm=False)

    # Bulk delete live with confirm
    bulk_res = await server.bulk_delete_items(item_ids=["1", "2"], dry_run=False, confirm=True)
    assert bulk_res["status"] == "success"
    assert bulk_res["deleted_count"] == 2


@pytest.mark.asyncio
async def test_server_read_only_mode(mock_transport, monkeypatch):
    async_client = httpx.AsyncClient(transport=mock_transport, base_url="https://api.example.com")
    monkeypatch.setattr(server, "client", server.TemplateClient(http_client=async_client))
    monkeypatch.setattr(settings, "MCP_READONLY", True)

    create_res = await server.create_item(name="Forbidden")
    assert create_res["status"] == "error"
    assert "read-only" in create_res["message"]

    delete_res = await server.delete_item(item_id="1", confirm=True)
    assert delete_res["status"] == "error"
    assert "read-only" in delete_res["message"]

    bulk_res = await server.bulk_delete_items(item_ids=["1"], dry_run=True)
    assert bulk_res["status"] == "error"
    assert "read-only" in bulk_res["message"]


@pytest.mark.asyncio
async def test_server_error_handling(monkeypatch):
    def error_transport(request: httpx.Request) -> httpx.Response:
        raise httpx.RequestError("Bearer secret-token-abc failure")

    async_client = httpx.AsyncClient(
        transport=httpx.MockTransport(error_transport), base_url="https://api.example.com"
    )
    monkeypatch.setattr(
        server, "client", server.TemplateClient(http_client=async_client, max_retries=0)
    )

    health = await server.get_health_status()
    assert health["status"] == "error"
    assert "Bearer [REDACTED]" in health["message"]

    items = await server.list_items()
    assert items["status"] == "error"

    item = await server.get_item("1")
    assert item["status"] == "error"

    create_res = await server.create_item("Test")
    assert create_res["status"] == "error"

    delete_res = await server.delete_item("1", confirm=True)
    assert delete_res["status"] == "error"

    monkeypatch.setattr(settings, "MCP_ALLOW_BULK_DESTRUCTIVE", True)
    bulk_res = await server.bulk_delete_items(["1"], dry_run=False, confirm=True)
    assert bulk_res["status"] == "error"


@pytest.mark.asyncio
async def test_mrtr_elicitation(mock_transport, monkeypatch):
    async_client = httpx.AsyncClient(transport=mock_transport, base_url="https://api.example.com")
    monkeypatch.setattr(server, "client", server.TemplateClient(http_client=async_client))

    # 1. delete_item: MRTR accepted
    ctx_accept = MagicMock()
    ctx_accept.elicit = AsyncMock(
        return_value=SimpleNamespace(action="accept", data=SimpleNamespace(confirm=True))
    )
    res_accept = await server.delete_item("123", confirm=False, ctx=ctx_accept)
    assert res_accept["status"] == "success"
    assert res_accept["deleted"] is True

    # 2. delete_item: MRTR declined
    ctx_decline = MagicMock()
    ctx_decline.elicit = AsyncMock(return_value=SimpleNamespace(action="decline", data=None))
    with pytest.raises(SafetyViolationError):
        await server.delete_item("123", confirm=False, ctx=ctx_decline)

    # 3. delete_item: MRTR error fallback
    ctx_err = MagicMock()
    ctx_err.elicit = AsyncMock(side_effect=RuntimeError("Elicitation unsupported"))
    with pytest.raises(SafetyViolationError):
        await server.delete_item("123", confirm=False, ctx=ctx_err)

    # 4. bulk_delete_items: MRTR accepted
    monkeypatch.setattr(settings, "MCP_ALLOW_BULK_DESTRUCTIVE", True)
    bulk_accept = await server.bulk_delete_items(
        ["1", "2"], dry_run=False, confirm=False, ctx=ctx_accept
    )
    assert bulk_accept["status"] == "success"
    assert bulk_accept["deleted_count"] == 2

    # 5. bulk_delete_items: MRTR declined
    with pytest.raises(SafetyViolationError):
        await server.bulk_delete_items(["1", "2"], dry_run=False, confirm=False, ctx=ctx_decline)

    # 6. bulk_delete_items: MRTR error fallback
    with pytest.raises(SafetyViolationError):
        await server.bulk_delete_items(["1", "2"], dry_run=False, confirm=False, ctx=ctx_err)


def test_server_resources_and_prompts():
    cap = server.get_capabilities()
    assert "Template MCP Server capabilities" in cap
    assert "MRTR Elicitations" in cap

    p = server.analyze_item_prompt(item_id="item-456")
    assert "item 'item-456'" in p


def test_cache_hints():
    assert "tools/list" in server.CACHE_HINTS
    assert server.CACHE_HINTS["tools/list"].ttl_ms == 3600000
    assert server.CACHE_HINTS["tools/list"].scope == "public"


def test_server_main_transports(monkeypatch):
    run_args = {}

    def fake_run(**kwargs):
        nonlocal run_args
        run_args = kwargs

    monkeypatch.setattr(server.mcp, "run", fake_run)

    # stdio
    monkeypatch.setattr("sys.argv", ["template-mcp", "--transport", "stdio"])
    server.main()
    assert run_args.get("transport") == "stdio"

    # streamable-http
    monkeypatch.setattr(
        "sys.argv",
        ["template-mcp", "--transport", "streamable-http", "--port", "9000"],
    )
    server.main()
    assert run_args.get("transport") == "streamable-http"
    assert run_args.get("port") == 9000

    # sse
    monkeypatch.setattr("sys.argv", ["template-mcp", "--transport", "sse", "--port", "9001"])
    server.main()
    assert run_args.get("transport") == "sse"
    assert run_args.get("port") == 9001


def test_handle_shutdown():
    with patch("os._exit") as mock_exit:
        server._handle_shutdown(15, None)
        mock_exit.assert_called_once_with(0)
