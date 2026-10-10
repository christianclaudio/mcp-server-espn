"""Recorded OpenTelemetry spans of a failing tool hold no secret.

FastMCP traces every ``tools/call`` with OpenTelemetry and records the raised exception
on the span (``exception`` event with ``exception.message`` and ``exception.stacktrace``).
A chain-walking exporter would bring back an unredacted original if the redacted
``ToolError`` still carried it on ``__cause__`` / ``__context__``.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import Client
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import espn_mcp.server as server
from espn_mcp.client import ESPNClient

EXPORTER = InMemorySpanExporter()


@pytest.fixture(scope="module", autouse=True)
def _provider() -> None:
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(EXPORTER))
    trace.set_tracer_provider(provider)


def _span_text(span: Any) -> str:
    parts = [span.name, str(span.status.description or "")]
    parts += [f"{k}={v}" for k, v in (span.attributes or {}).items()]
    for event in span.events:
        parts.append(event.name)
        parts += [f"{k}={v}" for k, v in (event.attributes or {}).items()]
    return "\n".join(parts)


@pytest.mark.asyncio
async def test_failing_tool_spans_hold_no_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    inner, outer = "hunter2-inner-otel", "sk-outer-otel-secret-12345"
    quoted = "FAKE part2-otel"

    async def request(self: Any, method: str, path: str, **_: Any) -> Any:
        try:
            raise RuntimeError(f"pool failed password={inner}")
        except RuntimeError as exc:
            raise ValueError(
                f'upstream rejected Authorization: Bearer {outer} {{"token": "{quoted}"}}'
            ) from exc

    monkeypatch.setattr(ESPNClient, "request", request)
    EXPORTER.clear()
    async with Client(server.create_server()) as client:
        result = await client.call_tool("games_get_scoreboard_header", {}, raise_on_error=False)
    assert result.is_error

    spans = EXPORTER.get_finished_spans()
    assert spans, "FastMCP recorded no spans"
    tool_spans = [s for s in spans if "get_scoreboard_header" in _span_text(s)]
    assert tool_spans, [s.name for s in spans]
    assert any(e.name == "exception" for s in tool_spans for e in s.events), (
        "no exception event recorded, so the check below would prove nothing"
    )
    text = "\n".join(_span_text(s) for s in spans)
    assert "Bearer [REDACTED]" in text
    for secret in (inner, outer, quoted, "part2-otel"):
        assert secret not in text
