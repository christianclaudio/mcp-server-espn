"""Tests for ESPN error handling and secret redaction."""

from espn_mcp.errors import (
    AuthenticationError,
    ESPNConnectionError,
    ESPNError,
    ESPNNotFoundError,
    ESPNRateLimitError,
    ESPNValidationError,
    SafetyViolationError,
    redact_secrets,
)


def test_redact_secrets():
    """Verify regex-based secret redaction across bearer tokens, api keys, and passwords."""
    assert redact_secrets("") == ""
    assert "Bearer [REDACTED]" in redact_secrets("Authorization: Bearer my-secret-token-12345")
    assert "api_key=[REDACTED]" in redact_secrets("api_key=secret-key-12345678")
    assert "client_secret: [REDACTED]" in redact_secrets("client_secret: secret-value-99999")
    assert "password: [REDACTED]" in redact_secrets("password: supersecret123")


def test_custom_exceptions():
    """Verify exception hierarchy and secret redaction on error initialization."""
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

    auth_err = AuthenticationError("Auth failed")
    assert isinstance(auth_err, ESPNError)


def test_espn_error_redacts_embedded_json_by_value() -> None:
    """``ESPNError`` keeps a JSON body valid, closing ``"}`` included (template v1.6.0)."""
    import json

    err = ESPNError('HTTP 401: {"error": "bad", "password": "p w", "hint": "password=x y"}')
    body = json.loads(err.message.removeprefix("HTTP 401: "))
    assert body == {"error": "bad", "password": "[REDACTED]", "hint": "password=[REDACTED]"}
    assert str(err) == err.message


async def test_tool_wrappers_keep_json_error_bodies_valid() -> None:
    """Each domain wrapper reports a JSON error body redacted by value, still parseable."""
    import json

    import pytest
    from fastmcp.exceptions import ToolError

    from espn_mcp.tools.games import espn_tool as games_tool
    from espn_mcp.tools.news import espn_tool as news_tool
    from espn_mcp.tools.teams import espn_tool as teams_tool

    async def boom() -> None:
        raise ValueError('HTTP 400: {"token": "ab cd ef", "n": 1}')

    for wrap in (games_tool, teams_tool, news_tool):
        with pytest.raises(ToolError) as exc:
            await wrap(boom)()
        body = json.loads(str(exc.value).removeprefix("HTTP 400: "))
        assert body == {"token": "[REDACTED]", "n": 1}
