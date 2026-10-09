"""Every tool, resource and prompt the server exposes, exercised through a real client.

House standard, mandatory in every server built from this template. Conformance runs
the MCP suite's own fixtures, so it cannot see this server's resources or prompts; unit
tests that call ``mcp.read_resource()`` or ``mcp.render_prompt()`` skip the request
handler that serializes the result. This test builds the server the way ``main()`` does
(``create_server(profile=...)``, once per profile in ``PROFILES``) and talks to it over
an in-memory ``fastmcp.Client``, so the full ``tools/list``, ``resources/list``,
``resources/templates/list``, ``resources/read``, ``prompts/list`` and ``prompts/get``
paths run on the wire types a host receives. The test runs offline because none of its
calls reach the vendor API; any repo whose tools need a network mock should supply it
in its own ``conftest.py``.

The checks are generic. A server copied from the template keeps this file unchanged
except for the import line and the three fixture tables below:

* ``SURFACE_SETTINGS`` -- ``settings`` attributes to patch before ``create_server``
  (dummy credentials or base URLs a resource needs to build). Example:
  ``{"API_KEY": "dummy-key"}``.
* ``RESOURCE_TEMPLATE_URIS`` -- one concrete URI per resource template, keyed by the
  client-visible ``uriTemplate`` (after mount namespacing). A template without an entry
  fails the test, so a new template is covered on purpose. Example:
  ``{"data://items/{item_id}": "data://items/1"}``.
* ``PROMPT_ARGUMENTS`` -- explicit arguments for a prompt, keyed by the client-visible
  prompt name. Without an entry the test fills only the required arguments, using the
  JSON schema FastMCP appends to each non-``str`` argument's description (a ``str``
  argument gets ``"example"``). Add an entry when a required argument needs a specific
  value, such as an enum member the prompt validates or an ID the mock transport knows.
  Example: ``{"items_analyze_item": {"item_id": "1"}}``.

Each fixture URI must match its own template (``match_uri_template``), so a fixture
that points at a static resource fails instead of covering a template that is never
read. ``ESPN_MCP_READONLY`` changes ``tools/list`` on every profile, so each profile is
built twice, with the setting off and on.

Every profile must list at least one tool. Each tool's ``inputSchema`` (and
``outputSchema`` when present) must be an object schema that is itself valid JSON Schema
(draft 2020-12); every failure is collected and reported in one message.

On ``full`` the test also builds the server with Tool Search on and, when this FastMCP
build exports Code Mode, with Code Mode on. Each run lists the discovery tools, checks
their schemas, and fetches every flat ``full`` tool through the mode's own discovery tool
(``search_tools`` by exact name; Code Mode ``search`` and ``get_schema``), so a tool that the mode
drops, or a schema it breaks, fails the run. These runs never call a catalog tool
(``call_tool`` / ``execute``), so they stay offline too.

A server with no resources, templates or prompts passes those checks trivially.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.client.transports import FastMCPTransport
from fastmcp.resources.template import match_uri_template
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from mcp.types import (
    BlobResourceContents,
    CallToolResult,
    PromptArgument,
    TextContent,
    TextResourceContents,
    Tool,
)

from espn_mcp.config import settings
from espn_mcp.profiles import PROFILES
from espn_mcp.server import create_server

SURFACE_SETTINGS: dict[str, Any] = {}
RESOURCE_TEMPLATE_URIS: dict[str, str] = {}
PROMPT_ARGUMENTS: dict[str, dict[str, str]] = {
    "games_game_analysis": {"sport": "football", "league": "nfl", "event_id": "401872947"},
    "teams_team_evaluation": {"sport": "football", "league": "nfl", "team_id": "14"},
}

SurfaceClient = Client[FastMCPTransport]

_SCHEMA_NOTE = re.compile(r"following JSON schema: (\{.*\})\. Encode non-string values as JSON\.")
_SCALAR_EXAMPLES: dict[str, Any] = {
    "string": "example",
    "integer": 1,
    "number": 1,
    "boolean": False,
    "array": [],
    "object": {},
    "null": None,
}


@pytest.fixture(params=sorted(PROFILES))
def surface_profile(request: pytest.FixtureRequest) -> str:
    """One profile name from ``PROFILES``."""
    return str(request.param)


@pytest.fixture
def surface_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch ``SURFACE_SETTINGS`` onto ``settings`` before a server is built."""
    for name, value in SURFACE_SETTINGS.items():
        monkeypatch.setattr(settings, name, value)


@pytest.fixture(params=[False, True], ids=["default", "ESPN_MCP_READONLY"])
def surface_readonly(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Build each profile with the ``ESPN_MCP_READONLY`` setting off, then on."""
    monkeypatch.setattr(settings, "MCP_READONLY", request.param)


@pytest.fixture
def surface_client(
    surface_profile: str, surface_settings: None, surface_readonly: None
) -> SurfaceClient:
    """An in-memory client on the production server build for one profile."""
    return Client(create_server(profile=surface_profile))


def _example_for(schema: dict[str, Any]) -> Any:
    """Return a minimal value that satisfies a simple JSON schema."""
    if "enum" in schema:
        return schema["enum"][0]
    if "const" in schema:
        return schema["const"]
    for key in ("anyOf", "oneOf"):
        if key in schema:
            return _example_for(schema[key][0])
    kind = schema.get("type", "string")
    if isinstance(kind, list):
        kind = kind[0]
    return _SCALAR_EXAMPLES.get(kind, "example")


def _wire_value(argument: PromptArgument) -> str:
    """Return a minimal MCP prompt argument string (prompt arguments are strings on the wire)."""
    match = _SCHEMA_NOTE.search(argument.description or "")
    value = _example_for(json.loads(match.group(1))) if match else "example"
    return value if isinstance(value, str) else json.dumps(value)


def _has_content(item: TextResourceContents | BlobResourceContents) -> bool:
    if isinstance(item, TextResourceContents):
        return bool(item.text)
    return bool(item.blob)


def _schema_failures(tools: list[Tool]) -> list[str]:
    """Collect every missing name, non-object schema and invalid JSON Schema in ``tools``."""
    failures: list[str] = []
    for tool in tools:
        schema = tool.input_schema
        if not tool.name or not isinstance(schema, dict) or schema.get("type") != "object":
            failures.append(f"{tool.name!r}: missing name or non-object input schema")
            continue
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            failures.append(f"{tool.name}: invalid input schema: {exc.message}")
        output = tool.output_schema
        if output is None:
            continue
        if not isinstance(output, dict) or output.get("type") != "object":
            failures.append(f"{tool.name}: non-object output schema")
            continue
        try:
            Draft202012Validator.check_schema(output)
        except SchemaError as exc:
            failures.append(f"{tool.name}: invalid output schema: {exc.message}")
    return failures


def _tools_from_json(result: CallToolResult) -> tuple[list[Tool], list[str]]:
    """Parse a discovery tool's JSON list of tool definitions (plus any ``not_found`` names)."""
    text = "".join(item.text for item in result.content if isinstance(item, TextContent))
    tools: list[Tool] = []
    missing: list[str] = []
    for entry in json.loads(text) if text else []:
        if "name" in entry:
            tools.append(Tool.model_validate(entry))
        else:
            missing.extend(entry.get("not_found", []))
    return tools, missing


async def _flat_full_tool_names() -> list[str]:
    """Tool names on the flat ``full`` build, with both discovery modes off."""
    flat = create_server(profile="full", enable_tool_search=False, enable_code_mode=False)
    async with Client(flat) as client:
        return sorted(tool.name for tool in await client.list_tools())


async def test_every_tool_lists_with_name_and_input_schema(
    surface_profile: str, surface_client: SurfaceClient
) -> None:
    """``tools/list`` returns at least one tool, each with a name and valid object schemas."""
    async with surface_client as client:
        tools = await client.list_tools()
    assert tools, f"profile {surface_profile!r} lists no tools"
    failures = _schema_failures(tools)
    assert not failures, "tools/list failed:\n" + "\n".join(failures)


async def test_full_tool_search_reaches_every_tool(surface_settings: None) -> None:
    """With Tool Search on ``full``, ``search_tools`` returns every flat tool with valid schemas."""
    expected = await _flat_full_tool_names()
    server = create_server(
        profile="full",
        enable_tool_search=True,
        enable_code_mode=False,
        tool_search_backend="regex",
    )
    found: dict[str, Tool] = {}
    async with Client(server) as client:
        listed = await client.list_tools()
        for name in expected:
            result = await client.call_tool("search_tools", {"pattern": rf"\b{re.escape(name)}\b"})
            hits, _ = _tools_from_json(result)
            found.update((tool.name, tool) for tool in hits if tool.name == name)
    failures = _schema_failures(listed)
    names = sorted(tool.name for tool in listed)
    if names != ["call_tool", "search_tools"]:
        failures.append(f"tools/list: expected search_tools and call_tool, got {names}")
    failures += _schema_failures(list(found.values()))
    failures += [f"search_tools: {name} not found" for name in expected if name not in found]
    assert not failures, "Tool Search run failed:\n" + "\n".join(failures)


async def test_full_code_mode_reaches_every_tool(surface_settings: None) -> None:
    """With Code Mode on ``full``, ``search`` and ``get_schema`` reach every flat tool."""
    pytest.importorskip("fastmcp.experimental.transforms.code_mode")
    expected = await _flat_full_tool_names()
    server = create_server(profile="full", enable_tool_search=False, enable_code_mode=True)
    failures: list[str] = []
    async with Client(server) as client:
        listed = await client.list_tools()
        for name in expected:
            hits = await client.call_tool("search", {"query": name})
            text = "".join(item.text for item in hits.content if isinstance(item, TextContent))
            if f"- {name}:" not in text:
                failures.append(f"search: {name} not found")
        schemas = await client.call_tool("get_schema", {"tools": expected, "detail": "full"})
    failures += _schema_failures(listed)
    names = sorted(tool.name for tool in listed)
    if "execute" not in names or set(names) & set(expected):
        failures.append(f"tools/list: expected Code Mode meta-tools only, got {names}")
    found, missing = _tools_from_json(schemas)
    failures += _schema_failures(found)
    failures += [f"get_schema: {name} not found" for name in missing]
    if sorted(tool.name for tool in found) != expected:
        failures.append(f"get_schema: expected {expected}, got {sorted(t.name for t in found)}")
    assert not failures, "Code Mode run failed:\n" + "\n".join(failures)


async def test_every_resource_reads_through_the_client(surface_client: SurfaceClient) -> None:
    """Every concrete resource, and one fixture URI per template, reads with content."""
    failures: list[str] = []
    async with surface_client as client:
        uris = [str(resource.uri) for resource in await client.list_resources()]
        for template in await client.list_resource_templates():
            fixture = RESOURCE_TEMPLATE_URIS.get(template.uri_template)
            if fixture is None:
                failures.append(f"{template.uri_template}: no RESOURCE_TEMPLATE_URIS entry")
            elif match_uri_template(fixture, template.uri_template) is None:
                failures.append(f"{template.uri_template}: fixture {fixture} does not match")
            else:
                uris.append(fixture)
        for uri in uris:
            try:
                contents = await client.read_resource(uri)
            except Exception as exc:
                failures.append(f"{uri}: {type(exc).__name__}: {exc}")
                continue
            if not contents or not all(_has_content(item) for item in contents):
                failures.append(f"{uri}: empty contents")
    assert not failures, "resources/read failed:\n" + "\n".join(failures)


async def test_every_prompt_renders_through_the_client(surface_client: SurfaceClient) -> None:
    """Every prompt renders with its required arguments filled from the declared schema."""
    failures: list[str] = []
    async with surface_client as client:
        for prompt in await client.list_prompts():
            arguments = PROMPT_ARGUMENTS.get(prompt.name)
            if arguments is None:
                arguments = {
                    argument.name: _wire_value(argument)
                    for argument in prompt.arguments or []
                    if argument.required
                }
            try:
                result = await client.get_prompt(prompt.name, arguments)
            except Exception as exc:
                failures.append(f"{prompt.name}: {type(exc).__name__}: {exc}")
                continue
            if not result.messages:
                failures.append(f"{prompt.name}: no messages")
    assert not failures, "prompts/get failed:\n" + "\n".join(failures)
