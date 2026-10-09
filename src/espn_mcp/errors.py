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
    # Bearer value: base64url and base64 characters (``~``, ``+``, ``/``) plus ``=``
    # padding.
    re.compile(r"(?i)(bearer\s+)[a-z0-9_\-\.~+/]{8,}=*", re.IGNORECASE),
    re.compile(r"(?i)(api[_-]?key[\"'\s:=]+)[a-z0-9_\-\.]{8,}", re.IGNORECASE),
    re.compile(r"(?i)(client[_-]?secret[\"'\s:=]+)[a-z0-9_\-\.]{8,}", re.IGNORECASE),
    re.compile(r"(?i)(password[\"'\s:=]+)[^\s\"',]{4,}", re.IGNORECASE),
    # api/access/refresh/auth/id/session tokens as key=value, key: value, an
    # ``X-Auth-Token:`` header and JSON ("key": "value", also backslash-escaped inside an
    # already-serialized JSON string).
    re.compile(
        r"(?i)((?:api|access|refresh|auth|id|session)[_-]?token(?:\\?[\"'])?\s*[:=]\s*"
        r"(?:\\?[\"'])?)[^\s\"'\\&,;]+",
        re.IGNORECASE,
    ),
    # The same keys URL-encoded (``access_token%3D...``); the value stops at an encoded
    # ``%26`` (&) or ``%23`` (#), so the parameters after it survive.
    re.compile(
        r"(?i)((?:api|access|refresh|auth|id|session)[_-]?token%3D)"
        r"(?:[^\s\"'\\&,;#%]|%(?!26|23))+",
        re.IGNORECASE,
    ),
    # ``Authorization: Token <value>`` scheme, also as a quoted JSON or dict entry.
    re.compile(
        r"(?i)(authorization(?:\\?[\"'])?\s*[:=]\s*(?:\\?[\"'])?token\s+)[^\s\"'\\&,;]+",
        re.IGNORECASE,
    ),
    # JSON ``"token": "value"``; the opening quote right before ``token`` keeps keys such
    # as ``"next_token"`` and ``"page_token"`` untouched.
    re.compile(r"(?i)(\\?[\"']token\\?[\"']\s*:\s*\\?[\"'])[^\s\"'\\&,;]+", re.IGNORECASE),
    # Bare ``token`` key with ``:`` or ``=``, optional spaces and an optional opening
    # quote (``token=``, ``token: x``, ``token = x``, ``token: "x"``); the lookbehind
    # keeps ``page_token``, ``next_token``, ``csrf_token`` and ``max_tokens`` untouched.
    re.compile(
        r"(?i)((?<![A-Za-z0-9_])token\s*[:=]\s*(?:\\?[\"'])?)[^\s\"'\\&#]+",
        re.IGNORECASE,
    ),
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
    so the ``tools/call`` result has ``isError: true`` (MCP tool error handling). It is raised
    after the ``except`` block ends, ``from None``, with any context Python attaches from the
    caller cleared, so the unredacted original exception never rides along as ``__cause__`` or
    ``__context__`` (tracebacks, OpenTelemetry exception events, chain-walking reporters).
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
            failure = ToolError(redact_secrets(str(exc)))
        else:
            return {"status": "success", "data": data}
        try:
            raise failure from None
        finally:
            failure.__context__ = None

    return wrapper


# Backwards compatibility aliases
TemplateError = ESPNError
ResourceNotFoundError = ESPNNotFoundError
RateLimitError = ESPNRateLimitError
