"""Tests for ESPN error handling and secret redaction."""

import pytest
from fastmcp.exceptions import ToolError

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


def test_redact_secrets_token_forms() -> None:
    """api/access/refresh tokens and a bare token= query parameter are redacted in every form."""
    cases = {
        "api_token=SECRET1": "api_token=[REDACTED]",
        "api-token: SECRET1": "api-token: [REDACTED]",
        "access_token: SECRET2": "access_token: [REDACTED]",
        '{"refresh_token": "SECRET4"}': '{"refresh_token": "[REDACTED]"}',
        '{"m": "{\\"access_token\\": \\"SECRET5\\"}"}': (
            '{"m": "{\\"access_token\\": \\"[REDACTED]\\"}"}'
        ),
        "GET https://api.example.com/x?token=SECRET3&page=2": (
            "GET https://api.example.com/x?token=[REDACTED]&page=2"
        ),
        "url=/x?a=1&TOKEN=SECRET6": "url=/x?a=1&TOKEN=[REDACTED]",
        "refresh_token=a.b-c_d/e+f==": "refresh_token=[REDACTED]",
    }
    for raw, expected in cases.items():
        assert redact_secrets(raw) == expected, raw


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param("auth_token=SECRET7", "auth_token=[REDACTED]", id="auth_token"),
        pytest.param('{"id_token": "SECRET8"}', '{"id_token": "[REDACTED]"}', id="id_token"),
        pytest.param(
            "session-token: SECRET9&x=1", "session-token: [REDACTED]&x=1", id="session_token"
        ),
        pytest.param(
            "X-Auth-Token: SECRET10\nAccept: */*",
            "X-Auth-Token: [REDACTED]\nAccept: */*",
            id="x_auth_token_header",
        ),
        pytest.param(
            "Authorization: Token SECRET11 rejected",
            "Authorization: Token [REDACTED] rejected",
            id="authorization_token",
        ),
        pytest.param(
            "{'Authorization': 'Token SECRET12'}",
            "{'Authorization': 'Token [REDACTED]'}",
            id="authorization_token_dict",
        ),
        pytest.param('{"token": "SECRET13"}', '{"token": "[REDACTED]"}', id="json_token"),
        pytest.param(
            '{"m": "{\\"token\\": \\"SECRET14\\"}"}',
            '{"m": "{\\"token\\": \\"[REDACTED]\\"}"}',
            id="json_token_escaped",
        ),
        pytest.param(
            "cb=https%3A%2F%2Fh%2Fx%3Faccess_token%3DSECRET15%26x%3D1%23frag",
            "cb=https%3A%2F%2Fh%2Fx%3Faccess_token%3D[REDACTED]%26x%3D1%23frag",
            id="url_encoded_access_token",
        ),
        pytest.param(
            "cb=https%3A%2F%2Fh%2Fx%3Faccess_token%3DSECRET21%23frag",
            "cb=https%3A%2F%2Fh%2Fx%3Faccess_token%3D[REDACTED]%23frag",
            id="url_encoded_access_token_fragment",
        ),
        pytest.param(
            "q=api_token%3DS16%26refresh_token%3DS17%26auth_token%3DS18"
            "%26id_token%3DS19%26session_token%3DS20",
            "q=api_token%3D[REDACTED]%26refresh_token%3D[REDACTED]%26auth_token%3D[REDACTED]"
            "%26id_token%3D[REDACTED]%26session_token%3D[REDACTED]",
            id="url_encoded_other_keys",
        ),
    ],
)
def test_redact_secrets_more_token_forms(raw: str, expected: str) -> None:
    """auth/id/session tokens, X-Auth-Token, Authorization: Token, JSON "token" and %3D."""
    assert redact_secrets(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param("token: SECRET22", "token: [REDACTED]", id="token_colon_space"),
        pytest.param("token:SECRET23", "token:[REDACTED]", id="token_colon"),
        pytest.param(
            "token = SECRET24",
            "token = [REDACTED]",
            id="token_spaced_equals",
        ),
        pytest.param(
            "Token: abcdefgh12345",
            "Token: [REDACTED]",
            id="token_capitalized",
        ),
        pytest.param(
            'token: "SECRET25"',
            'token: "[REDACTED]"',
            id="token_colon_double_quote",
        ),
        pytest.param(
            "token='SECRET26'",
            "token='[REDACTED]'",
            id="token_equals_single_quote",
        ),
    ],
)
def test_redact_secrets_bare_token_colon_and_spaced(raw: str, expected: str) -> None:
    """A bare token key takes ``:`` or ``=``, optional spaces and a quote.

    The key, separator and quote are all kept.
    """
    assert redact_secrets(raw) == expected


def test_redact_secrets_bearer_base64_tail() -> None:
    """A bearer value with ``~``, ``/``, ``+`` and ``=`` padding is redacted.

    No tail of the value is left behind.
    """
    assert redact_secrets("Bearer abc.def~ghi/jk+l==") == "Bearer [REDACTED]"


def test_redact_secrets_token_query_stops_at_fragment() -> None:
    """A bare ``token=`` query value stops at a literal ``#``.

    The fragment after it survives.
    """
    assert redact_secrets("/x?token=SECRET#frag") == "/x?token=[REDACTED]#frag"


def test_redact_secrets_leaves_token_words_alone() -> None:
    """Ordinary words and pagination fields that contain "token" are not redacted."""
    for text in (
        "tokenizer failed on input",
        "next_page_token_count=5",
        "page_token=abc123 is a pagination cursor",
        "next_token=abc123&x=1",
        "csrf_token=abc123#frag",
        "refresh_token_expires_in=3600",
        "the token expired",
        "max_tokens=1024",
        '{"page_token": "x", "next_token": "x", "csrf_token": "x", "max_tokens": 5}',
        "X-Auth-Token-Expires: 2026-10-09T00:00:00Z",
        "session_token_ttl=3600",
        "id_token_hint_count=2",
        "Token x is invalid",
        "Authorization failed: token expired",
        "max_tokens: 5",
        "next_token: abc",
        "page_token: abc",
        "X-Auth-Token-Expires: 5",
        '{"token": null}',
    ):
        assert redact_secrets(text) == text, text


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
    # A FastMCP ToolError, so a refusal reaches the client as a result with isError: true
    assert isinstance(safety_err, ToolError)

    auth_err = AuthenticationError("Auth failed")
    assert isinstance(auth_err, ESPNError)
