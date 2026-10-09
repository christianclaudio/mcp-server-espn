"""Error structures and automated credential redaction for ESPN MCP."""

import functools
import logging
import re
import traceback
from collections.abc import Awaitable, Callable
from typing import Any

from fastmcp.exceptions import ToolError

logger = logging.getLogger(__name__)

# Regex patterns for sensitive tokens, bearer headers, and keys
SECRET_PATTERNS = [
    re.compile(r"(?i)(bearer\s+)[a-z0-9_\-\.]{8,}", re.IGNORECASE),
    re.compile(r"(?i)(api[_-]?key[\"'\s:=]+)[a-z0-9_\-\.]{8,}", re.IGNORECASE),
    re.compile(r"(?i)(client[_-]?secret[\"'\s:=]+)[a-z0-9_\-\.]{8,}", re.IGNORECASE),
    re.compile(r"(?i)(password[\"'\s:=]+)[^\s\"',]{4,}", re.IGNORECASE),
]


def redact_secrets(text: str) -> str:
    """Scrub sensitive credentials, tokens, and authorization headers from text."""
    if not text:
        return ""
    sanitized = text
    for pattern in SECRET_PATTERNS:
        sanitized = pattern.sub(r"\1[REDACTED]", sanitized)
    return sanitized


class ESPNError(Exception):
    """Base exception for all ESPN MCP errors with automatic message redaction."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        self.message = redact_secrets(message)
        self.details = details or {}
        super().__init__(self.message)


class ESPNConnectionError(ESPNError):
    """Raised on connection failures or upstream ESPN 5xx errors."""


class ESPNNotFoundError(ESPNError):
    """Raised when an ESPN resource (game event, team, player) is not found."""


class ESPNRateLimitError(ESPNError):
    """Raised on HTTP 429 rate limit exhaustion."""


class ESPNValidationError(ESPNError):
    """Raised on invalid sport, league, or parameter input."""


class SafetyViolationError(ESPNError, ToolError):
    """Raised when an operation violates safety gating (the read-only gate).

    Also a FastMCP ``ToolError``, so a refusal raised from middleware reaches the client as
    a ``tools/call`` result with ``isError: true`` instead of a JSON-RPC internal error.
    """


class AuthenticationError(ESPNError):
    """Raised on upstream authentication failures."""


def espn_tool(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Wrap a tool so success is ``{"status": "success", "data": ...}`` and failure is a ToolError.

    Any exception from the tool body (including ESPN upstream HTTP failures) is logged with a
    redacted traceback and re-raised as a FastMCP ``ToolError`` carrying the redacted message,
    so the ``tools/call`` result has ``isError: true`` (MCP tool error handling). FastMCP
    logs a ``ToolError`` without its traceback, so the unredacted cause is never logged.
    """

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            data = await fn(*args, **kwargs)
        except Exception as exc:
            logger.error(
                "Error executing %s: %s",
                fn.__name__,
                redact_secrets(traceback.format_exc()),
            )
            raise ToolError(redact_secrets(str(exc))) from exc
        return {"status": "success", "data": data}

    return wrapper


# Backwards compatibility aliases
TemplateError = ESPNError
ResourceNotFoundError = ESPNNotFoundError
RateLimitError = ESPNRateLimitError
