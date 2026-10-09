"""espn_tool error hygiene: no exception chain, and the decorator's own redaction.

The decorator raises ToolError ``from None`` so the unredacted original exception never
rides along as ``__cause__`` or ``__context__`` (tracebacks, OpenTelemetry exception
events). Its own ``redact_secrets`` calls must scrub a secret from an unexpected
(non-client) exception before it reaches the client or the logs.
"""

from __future__ import annotations

import logging

import pytest
from fastmcp.exceptions import ToolError

from espn_mcp.errors import espn_tool

_TOKEN = "hygiene-secret-token-0123456789"


@pytest.mark.asyncio
async def test_decorator_suppresses_the_original_exception() -> None:
    @espn_tool
    async def handler() -> None:
        raise RuntimeError(f"unexpected failure Bearer {_TOKEN}")

    with pytest.raises(ToolError) as exc_info:
        await handler()
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True


@pytest.mark.asyncio
async def test_decorator_redacts_an_unexpected_exception(caplog: pytest.LogCaptureFixture) -> None:
    """A non-client exception carrying a secret is redacted in the ToolError and the logs."""
    caplog.set_level(logging.DEBUG)

    @espn_tool
    async def handler() -> None:
        raise RuntimeError(f"connect failed: Authorization: Bearer {_TOKEN} api_key={_TOKEN}")

    with pytest.raises(ToolError) as exc_info:
        await handler()
    assert (
        str(exc_info.value) == "connect failed: Authorization: Bearer [REDACTED] api_key=[REDACTED]"
    )
    assert "Error executing handler" in caplog.text
    assert _TOKEN not in caplog.text
