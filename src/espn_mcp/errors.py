"""Error structures and automated credential redaction for ESPN MCP."""

import re
from typing import Any

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
        self.raw_message = message
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


class SafetyViolationError(ESPNError):
    """Raised when an operation violates safety gating."""


# Backwards compatibility aliases
TemplateError = ESPNError
AuthenticationError = ESPNError
ResourceNotFoundError = ESPNNotFoundError
RateLimitError = ESPNRateLimitError
