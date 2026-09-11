"""Protocol integration tests for MCPServer discovery and stateless HTTP."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

import httpx
import pytest

from espn_mcp.server import ESPNClient, mcp


def test_stdio_initialize_handshake() -> None:
    """Verify end-to-end JSON-RPC initialization handshake over stdio."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "espn_mcp.server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    init_payload: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2026-07-28",
            "capabilities": {},
            "clientInfo": {"name": "pytest-protocol-client", "version": "1.0.0"},
        },
    }

    try:
        stdout_data, stderr_data = proc.communicate(
            input=json.dumps(init_payload) + "\n", timeout=10
        )
    except subprocess.TimeoutExpired:
        proc.kill()
        pytest.fail("Stdio initialization handshake timed out.")

    lines = [line.strip() for line in stdout_data.split("\n") if line.strip()]
    assert lines, f"No stdio response received from server. Stderr: {stderr_data}"

    response = json.loads(lines[0])
    assert "result" in response, f"Invalid handshake response: {response}"
    assert "protocolVersion" in response["result"]
    assert response["result"]["serverInfo"]["name"] == "espn-mcp"


@pytest.mark.asyncio
async def test_dynamic_tools_listing() -> None:
    """Verify MCPServer dynamically advertises tools with valid schemas and annotations."""
    tools = await mcp.list_tools()
    assert len(tools) == 10

    for tool in tools:
        assert tool.name
        assert tool.description
        assert tool.input_schema is not None
        assert tool.annotations is not None
        assert hasattr(tool.annotations, "read_only_hint")
        assert hasattr(tool.annotations, "destructive_hint")


@pytest.mark.asyncio
async def test_dynamic_resources_and_prompts() -> None:
    """Verify native resources and prompts discovery on MCPServer."""
    resources = await mcp.list_resources()
    assert any(str(r.uri) == "espn://reference/capabilities" for r in resources)
    assert any(str(r.uri) == "espn://reference/supported-leagues" for r in resources)

    prompts = await mcp.list_prompts()
    assert any(p.name == "game_analysis" for p in prompts)
    assert any(p.name == "team_evaluation" for p in prompts)


@pytest.mark.asyncio
async def test_stateless_streamable_http_standalone_post(mock_transport, monkeypatch) -> None:
    """Verify Streamable HTTP in stateless mode accepts standalone requests without session ID."""
    async with httpx.AsyncClient(
        transport=mock_transport, base_url="https://site.web.api.espn.com"
    ) as async_client:
        import espn_mcp.server as srv

        monkeypatch.setattr(srv, "client", ESPNClient(http_client=async_client))

        app = mcp.streamable_http_app(stateless_http=True, json_response=True)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
            ) as http_c:
                # 1. Standalone initialize without session ID
                init_payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2026-07-28",
                        "capabilities": {},
                        "clientInfo": {"name": "test-stateless-client", "version": "1.0"},
                    },
                }
                init_resp = await http_c.post(
                    "/mcp",
                    json=init_payload,
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                )
                assert init_resp.status_code == 200
                init_data = init_resp.json()
                assert init_data["result"]["serverInfo"]["name"] == "espn-mcp"
                assert "mcp-session-id" not in init_resp.headers

                # 2. Standalone tools/list without session ID
                list_payload = {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/list",
                    "params": {},
                }
                list_resp = await http_c.post(
                    "/mcp",
                    json=list_payload,
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                )
                assert list_resp.status_code == 200
                list_data = list_resp.json()
                assert "tools" in list_data["result"]
                assert len(list_data["result"]["tools"]) == 10

                # 3. Standalone tools/call without session ID
                call_payload = {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "get_scoreboard",
                        "arguments": {"sport": "baseball", "league": "mlb", "date": "20260904"},
                    },
                }
                call_resp = await http_c.post(
                    "/mcp",
                    json=call_payload,
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                )
                assert call_resp.status_code == 200
                call_data = call_resp.json()
                assert "content" in call_data["result"]
                parsed_tool_res = json.loads(call_data["result"]["content"][0]["text"])
                assert parsed_tool_res["status"] == "success"
                assert parsed_tool_res["data"]["count"] == 1
