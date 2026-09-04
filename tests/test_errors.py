"""Tests for ESPN error handling and secret redaction."""

from espn_mcp.errors import (
    ESPNConnectionError,
    ESPNError,
    ESPNNotFoundError,
    ESPNRateLimitError,
    ESPNValidationError,
    SafetyViolationError,
    redact_secrets,
)


def test_redact_secrets():
    assert redact_secrets("") == ""
    assert "Bearer [REDACTED]" in redact_secrets("Authorization: Bearer my-secret-token-12345")
    assert "api_key=[REDACTED]" in redact_secrets("api_key=secret-key-12345678")
    assert "client_secret: [REDACTED]" in redact_secrets("client_secret: secret-value-99999")
    assert "password: [REDACTED]" in redact_secrets("password: supersecret123")


def test_custom_exceptions():
    err = ESPNError("Bearer secret-token-abcdefgh", details={"code": 100})
    assert "Bearer [REDACTED]" in err.message
    assert err.details["code"] == 100

    conn_err = ESPNConnectionError("Connection failed")
    assert isinstance(conn_err, ESPNError)

    not_found = ESPNNotFoundError("Not found")
    assert isinstance(not_found, ESPNError)

    rate_err = ESPNRateLimitError("Rate limit")
    assert isinstance(rate_err, ESPNError)

    val_err = ESPNValidationError("Validation error")
    assert isinstance(val_err, ESPNError)

    safety_err = SafetyViolationError("Safety violated")
    assert isinstance(safety_err, ESPNError)
