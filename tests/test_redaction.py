"""Template v1.6.0 redaction tests, ported as they are (``TemplateError`` is ``ESPNError``)."""

import json

import pytest
from fastmcp.exceptions import ToolError

from espn_mcp.errors import (
    AuthenticationError,
    RateLimitError,
    ResourceNotFoundError,
    SafetyViolationError,
    TemplateError,
    redact_message,
    redact_secrets,
    tool_error,
    tool_failure,
)


def test_redact_secrets() -> None:
    """Verify regex secret redaction across standard sensitive key patterns."""
    assert redact_secrets("") == ""
    assert "Bearer [REDACTED]" in redact_secrets("Authorization: Bearer my-secret-token-12345")
    assert "api_key=[REDACTED]" in redact_secrets("api_key=secret-key-12345678")
    assert "client_secret: [REDACTED]" in redact_secrets("client_secret: secret-value-99999")
    assert "password: [REDACTED]" in redact_secrets("password: supersecret123")


def test_custom_exceptions() -> None:
    """Verify TemplateError and its specialized subclasses redact secrets and store details."""
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


def test_tool_error_redacts_the_exception_message() -> None:
    """tool_error() redacts the message itself; a plain exception carries the raw secret."""
    secret = "sk-live-tool-error-0123456789"
    err = tool_error(RuntimeError(f"upstream 401 for Authorization: Bearer {secret}"))
    assert isinstance(err, ToolError)
    assert secret not in str(err)
    assert str(err) == "upstream 401 for Authorization: Bearer [REDACTED]"


def test_tool_failure_is_redacted_json() -> None:
    """tool_failure() carries the {"error": {...}} JSON, redacted as a whole document."""
    err = tool_failure(
        "batch_failed", "Bearer secret-token-abc", errors=[{"detail": "Bearer secret-token-xyz"}]
    )
    assert isinstance(err, ToolError)
    payload = json.loads(str(err))["error"]
    assert payload == {
        "type": "batch_failed",
        "message": "Bearer [REDACTED]",
        "errors": [{"detail": "Bearer [REDACTED]"}],
    }


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
        pytest.param("token = SECRET24", "token = [REDACTED]", id="token_spaced_equals"),
        pytest.param("Token: abcdefgh12345", "Token: [REDACTED]", id="token_capitalized"),
        pytest.param('token: "SECRET25"', 'token: "[REDACTED]"', id="token_colon_double_quote"),
        pytest.param("token='SECRET26'", "token='[REDACTED]'", id="token_equals_single_quote"),
    ],
)
def test_redact_secrets_bare_token_colon_and_spaced(raw: str, expected: str) -> None:
    """A bare token key takes ``:`` or ``=``, optional spaces and a quote; all are kept."""
    assert redact_secrets(raw) == expected


def test_redact_secrets_bearer_base64_tail() -> None:
    """A bearer value with ``~``, ``/``, ``+`` and ``=`` padding is redacted with no tail left."""
    assert redact_secrets("Bearer abc.def~ghi/jk+l==") == "Bearer [REDACTED]"


def test_redact_secrets_token_query_stops_at_fragment() -> None:
    """A bare ``token=`` query value stops at a literal ``#``, so the fragment survives."""
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


def test_tool_failure_with_password_stays_valid_json() -> None:
    """key=value inside a JSON message is redacted whole on the decoded string, before
    ``json.dumps``, so no part of the password is left and the message still parses."""
    err = tool_failure(
        "batch_failed",
        'login failed password=hun"ter,2}',
        errors=[{"detail": "password=a\\b,c}d", "next": "login failed password=hunter2"}],
    )
    payload = json.loads(str(err))
    rendered = str(err)
    for part in ("hun", "ter", "a\\b", "c}d", "hunter2"):
        assert part not in rendered, part
    assert payload["error"]["message"] == "login failed password=[REDACTED]"
    assert payload["error"]["errors"][0]["next"] == "login failed password=[REDACTED]"


def test_tool_failure_masks_values_under_credential_keys() -> None:
    """A detail under a credential key is masked outright; other keys are walked."""
    err = tool_failure(
        "batch_failed",
        "x",
        password='p,}\\"w',
        access_token="t0k",
        nested={"api_key": "k", "items": ["password=z,}", 3, None]},
    )
    payload = json.loads(str(err))["error"]
    assert payload["password"] == "[REDACTED]"
    assert payload["access_token"] == "[REDACTED]"
    assert payload["nested"] == {"api_key": "[REDACTED]", "items": ["password=[REDACTED]", 3, None]}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param("password=pw,99}x y", "password=[REDACTED]", id="kv_comma_brace"),
        pytest.param("password=a\\b\\c\nnext", "password=[REDACTED]\nnext", id="kv_backslash"),
        pytest.param("{password: FAKE,PW}", "{password: [REDACTED]", id="kv_unquoted_object"),
        pytest.param(
            '{"password": "p,w}\\\\x\\"y"}', '{"password": "[REDACTED]"}', id="json_punct"
        ),
        pytest.param("{'password': 'p,w}\\'x'}", "{'password': '[REDACTED]'}", id="python_repr"),
        pytest.param(
            '{\\"password\\": \\"p,w}\\\\\\\\x\\\\\\"y\\"}',
            '{\\"password\\": \\"[REDACTED]\\"}',
            id="escaped_json_punct",
        ),
        pytest.param("token=SECRET1,x=1", "token=[REDACTED],x=1", id="token_comma"),
        pytest.param("{token: SECRET2}", "{token: [REDACTED]}", id="token_brace"),
    ],
)
def test_redact_secrets_whole_value(raw: str, expected: str) -> None:
    """Passwords with commas, braces, backslashes and quotes are redacted whole."""
    assert redact_secrets(raw) == expected


def test_redact_secrets_json_password_with_punctuation_still_parses() -> None:
    """Redacting raw JSON text keeps it valid and drops every part of the value."""
    secret = 'a,b}c\\d"e'
    raw = json.dumps({"password": secret, "n": 1})
    out = redact_secrets(raw)
    assert json.loads(out) == {"password": "[REDACTED]", "n": 1}
    nested = json.dumps({"m": raw})
    out2 = redact_secrets(nested)
    assert json.loads(json.loads(out2)["m"]) == {"password": "[REDACTED]", "n": 1}
    for part in ("a,b", "c\\d", 'e"'):
        assert part not in out and part not in out2


def test_redact_secrets_pem_block() -> None:
    """A multi-line PEM block is redacted from BEGIN to END, also inside JSON text."""
    pem = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBg\nkqhkiG9w0BAQEF\n-----END PRIVATE KEY-----"
    out = redact_secrets(f"key load failed: {pem} (pkcs8)")
    assert out == "key load failed: [REDACTED] (pkcs8)"
    body = json.dumps({"private_key": pem})
    assert json.loads(redact_secrets(body)) == {"private_key": "[REDACTED]"}
    for part in ("MIIEvQ", "kqhkiG", "BEGIN", "END"):
        assert part not in out


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param(
            '{\\"api_key\\": \\"FAKEKEY12345\\"}',
            '{\\"api_key\\": \\"[REDACTED]\\"}',
            id="api_key",
        ),
        pytest.param(
            '{\\"password\\": \\"fakepw99\\"}',
            '{\\"password\\": \\"[REDACTED]\\"}',
            id="password",
        ),
        pytest.param(
            '{\\"client_secret\\": \\"FAKESEC12345\\"}',
            '{\\"client_secret\\": \\"[REDACTED]\\"}',
            id="client_secret",
        ),
        pytest.param(
            '{\\"private_key\\": \\"FAKEPRIVKEY123\\"}',
            '{\\"private_key\\": \\"[REDACTED]\\"}',
            id="private_key",
        ),
    ],
)
def test_redact_secrets_escaped_json_keys(raw: str, expected: str) -> None:
    """Template #68: keys inside an already-serialized JSON string keep their escaped quotes."""
    assert redact_secrets(raw) == expected


def test_tool_failure_redacts_nested_json_password() -> None:
    """A per-item error holding a JSON body is escaped by json.dumps and still redacted."""
    err = tool_failure("batch_failed", "x", errors=[{"detail": '{"password": "fakepw99"}'}])
    assert "fakepw99" not in str(err)
    assert json.loads(str(err))["error"]["errors"][0]["detail"] == '{"password": "[REDACTED]"}'


@pytest.mark.parametrize(
    ("template", "redacted"),
    [
        pytest.param("password={}", "password=[REDACTED]", id="password_equals"),
        pytest.param('{{"password": "{}"}}', '{"password": "[REDACTED]"}', id="password_json"),
        pytest.param("api_key={}", "api_key=[REDACTED]", id="api_key"),
        pytest.param("client_secret: {}", "client_secret: [REDACTED]", id="client_secret"),
        pytest.param(
            "Authorization: Bearer {}", "Authorization: Bearer [REDACTED]", id="authorization"
        ),
    ],
)
def test_redact_secrets_short_values_use_a_fixed_mask(template: str, redacted: str) -> None:
    """Template #67: 1-, 3- and 20-character secrets give the identical masked output."""
    outputs = {redact_secrets(template.format(secret)) for secret in ("x", "abc", "a" * 20)}
    assert outputs == {redacted}


def test_redact_secrets_short_value_near_misses() -> None:
    """Prose and whitespace-separated short words are not treated as keyed secrets."""
    for text in (
        "Bearer of bad news",
        "api key missing",
        "client secret rotated",
        "password is required",
        "invalid password",
        "the api_key field",
        "set a password and retry",
    ):
        assert redact_secrets(text) == text, text


def test_redact_secrets_long_input_is_linear() -> None:
    """Long hostile inputs finish quickly: no catastrophic backtracking in the patterns."""
    import time

    hostile = [
        "password" + ' \\"' * 20000,
        "api_key" + ":=" * 20000 + "!",
        "Authorization: Bearer " + "a" * 50000 + "!",
        "token=" + "a" * 50000 + "}",
    ]
    start = time.perf_counter()
    for text in hostile:
        redact_secrets(text)
    assert time.perf_counter() - start < 2.0


def test_tool_error_redacts_embedded_json_value_by_value() -> None:
    """JSON after a prefix is parsed, redacted per value and re-serialized; it still parses."""
    err = tool_error(
        RuntimeError('HTTP 400: {"message": "login failed password=hunter2", "code": 7}')
    )
    text = str(err)
    assert "hunter2" not in text and "hunt" not in text
    assert text.startswith("HTTP 400: ")
    body = json.loads(text[len("HTTP 400: ") :])
    assert body == {"message": "login failed password=[REDACTED]", "code": 7}


def test_tool_error_masks_credential_keys_in_embedded_json() -> None:
    """A credential key inside parsed JSON is masked outright; text after the JSON is redacted."""
    text = str(tool_error(RuntimeError('[{"api_key": "abc"}] then token=zzz9 end')))
    assert "abc" not in text and "zzz9" not in text
    assert json.loads(text.split(" then ")[0]) == [{"api_key": "[REDACTED]"}]


def test_tool_error_malformed_json_falls_back_to_full_redaction() -> None:
    """Unparseable JSON falls back to the regex: the secret goes whole, shape may break."""
    text = str(tool_error(RuntimeError('HTTP 400: {"message": "password=hunter2", "code": 7')))
    assert "hunter2" not in text and "hunt" not in text
    assert text.startswith("HTTP 400: ")


def test_tool_error_json_after_credential_key_is_masked_whole() -> None:
    """``password={...}`` parses, and the whole value after the key is masked."""
    text = str(tool_error(RuntimeError('password={"a": "s3cretvalue"}')))
    assert "s3cret" not in text


def test_redact_message_empty_and_plain() -> None:
    """Empty text stays empty; text with no JSON goes straight through the regex."""
    from espn_mcp.errors import redact_message

    assert redact_message("") == ""
    assert redact_message("a [ b { c") == "a [ b { c"
    assert redact_message('[1] x "5"') == '[1] x "5"'


_NON_STRING_SECRETS = [
    (7777777, "7777777"),
    (["hunter2"], "hunter2"),
    ({"v": "hunter2"}, "hunter2"),
    ([{"deep": 31337.5}], "31337"),
]


@pytest.mark.parametrize(("value", "fragment"), _NON_STRING_SECRETS)
def test_tool_error_masks_non_string_credential_values(value: object, fragment: str) -> None:
    text = str(tool_error(ValueError(json.dumps({"password": value, "ok": 1}))))
    assert fragment not in text
    assert json.loads(text) == {"password": "[REDACTED]", "ok": 1}


@pytest.mark.parametrize(("value", "fragment"), _NON_STRING_SECRETS)
def test_tool_failure_masks_non_string_credential_values(value: object, fragment: str) -> None:
    text = str(tool_failure("auth", "bad login", password=value, api_key=value))
    assert fragment not in text
    error = json.loads(text)["error"]
    assert error["password"] == "[REDACTED]"
    assert error["api_key"] == "[REDACTED]"


def test_none_under_credential_key_is_kept() -> None:
    error = json.loads(str(tool_failure("auth", "missing", password=None)))["error"]
    assert error["password"] is None


# ── #69 item 2: an unquoted or unparseable value after a credential key ───────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param(
            'password={"a": broken pass word}', "password=[REDACTED]", id="broken_json_object"
        ),
        pytest.param(
            'password={"a": broken pass word} tail', "password=[REDACTED] tail", id="balanced_tail"
        ),
        pytest.param("password=pass word123", "password=[REDACTED]", id="unquoted_space"),
        pytest.param(
            "password=pass word123\nnext line", "password=[REDACTED]\nnext line", id="to_eol"
        ),
        pytest.param("password: two words\nok", "password: [REDACTED]\nok", id="colon_form"),
        pytest.param("x password=[1, two three] tail", "x password=[REDACTED] tail", id="list"),
        pytest.param(
            'password={"a": {"b": "}]"}, c d} after',
            "password=[REDACTED] after",
            id="brackets_inside_a_string",
        ),
        pytest.param(
            "password={never closed\nnext", "password=[REDACTED]\nnext", id="unbalanced_to_eol"
        ),
        pytest.param("api_key={x: oops} next", "api_key=[REDACTED] next", id="api_key_object"),
    ],
)
def test_redact_unparseable_value_after_key(raw: str, expected: str) -> None:
    assert redact_secrets(raw) == expected
    assert redact_message(raw) == expected


def test_bracket_value_scan_is_linear_on_long_input() -> None:
    """Hostile bracket input finishes quickly: no catastrophic backtracking."""
    import time

    hostile = [
        "password=" + "{" * 50000,
        "password=" + "[" * 25000 + "]" * 25000,
        "password={" + '"\\' * 30000,
        "password=" + "{a" * 20000 + "\n" + "x" * 50000,
        "password={ " * 20000,
        "password=x " * 20000,
    ]
    start = time.perf_counter()
    for text in hostile:
        assert redact_secrets(text).startswith("password=[REDACTED]")
    assert time.perf_counter() - start < 2.0


# ── #69 item 4: quoted token values with spaces are redacted to the closing quote ─


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param('{"token": "FAKE part2"}', '{"token": "[REDACTED]"}', id="json_token"),
        pytest.param("{'token': 'FAKE part2'}", "{'token': '[REDACTED]'}", id="repr_token"),
        pytest.param('token="FAKE part2" next', 'token="[REDACTED]" next', id="bare_token"),
        pytest.param('token: "a \\" b" n', 'token: "[REDACTED]" n', id="escaped_quote_inside"),
        pytest.param(
            '{"access_token": "FAKE part2", "n": 1}',
            '{"access_token": "[REDACTED]", "n": 1}',
            id="access_token",
        ),
        pytest.param(
            "session_token='FAKE part2'&x=1", "session_token='[REDACTED]'&x=1", id="session"
        ),
        pytest.param(
            '{\\"token\\": \\"FAKE part2\\"}', '{\\"token\\": \\"[REDACTED]\\"}', id="escaped_json"
        ),
        pytest.param(
            '{\\"refresh_token\\": \\"FAKE part2\\", \\"n\\": 1}',
            '{\\"refresh_token\\": \\"[REDACTED]\\", \\"n\\": 1}',
            id="escaped_refresh",
        ),
        pytest.param('"page_token": "keep me"', '"page_token": "keep me"', id="page_token_kept"),
        pytest.param("token=SECRET1 tail", "token=[REDACTED] tail", id="unquoted_unchanged"),
    ],
)
def test_redact_quoted_token_with_spaces(raw: str, expected: str) -> None:
    assert redact_secrets(raw) == expected


def test_quoted_token_json_still_parses() -> None:
    raw = json.dumps({"token": "FAKE part2", "id_token": 'x "y" z', "n": 1})
    assert json.loads(redact_secrets(raw)) == {
        "token": "[REDACTED]",
        "id_token": "[REDACTED]",
        "n": 1,
    }
    nested = json.dumps({"m": raw})
    inner = json.loads(json.loads(redact_secrets(nested))["m"])
    assert inner == {"token": "[REDACTED]", "id_token": "[REDACTED]", "n": 1}


def test_quoted_token_scan_is_linear() -> None:
    import time

    hostile = ['"token": "' + "a \\" * 30000, 'access_token="' + "\\x " * 30000]
    start = time.perf_counter()
    for text in hostile:
        redact_secrets(text)
    assert time.perf_counter() - start < 2.0
