"""Error structures and automated credential redaction."""

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


class TemplateError(Exception):
    """Base exception for all template errors with automatic message redaction."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        self.raw_message = message
        self.message = redact_secrets(message)
        self.details = details or {}
        super().__init__(self.message)


class AuthenticationError(TemplateError):
    """Raised on HTTP 401 / 403 authorization failures."""


class ResourceNotFoundError(TemplateError):
    """Raised on HTTP 404 missing resource errors."""


class RateLimitError(TemplateError):
    """Raised on HTTP 429 rate limit exhaustion."""


class SafetyViolationError(TemplateError):
    """Raised when an operation violates single-delete or bulk-destructive safety gates."""
