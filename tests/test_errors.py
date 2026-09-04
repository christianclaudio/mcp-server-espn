"""Tests for error handling and secret redaction."""

from template_mcp.errors import (
    AuthenticationError,
    RateLimitError,
    ResourceNotFoundError,
    SafetyViolationError,
    TemplateError,
    redact_secrets,
)


def test_redact_secrets():
    assert redact_secrets("") == ""
    assert "Bearer [REDACTED]" in redact_secrets("Authorization: Bearer my-secret-token-12345")
    assert "api_key=[REDACTED]" in redact_secrets("api_key=secret-key-12345678")
    assert "client_secret: [REDACTED]" in redact_secrets("client_secret: secret-value-99999")
    assert "password: [REDACTED]" in redact_secrets("password: supersecret123")


def test_custom_exceptions():
    err = TemplateError("Bearer secret-token-abcdefgh", details={"code": 100})
    assert "Bearer [REDACTED]" in err.message
    assert err.details["code"] == 100

    auth_err = AuthenticationError("Auth failed")
    assert isinstance(auth_err, TemplateError)

    not_found = ResourceNotFoundError("Not found")
    assert isinstance(not_found, TemplateError)

    rate_err = RateLimitError("Rate limit")
    assert isinstance(rate_err, TemplateError)

    safety_err = SafetyViolationError("Safety violated")
    assert isinstance(safety_err, TemplateError)
