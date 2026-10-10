"""Single-quote, PEM END label and Python-repr redaction, ported from the template."""

from __future__ import annotations

import time

import pytest

from espn_mcp.errors import MASK, redact_message, redact_secrets, tool_error


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("{'api_key': ['S3CRET2','b']}", "{'api_key': [REDACTED]}"),
        ("{'api_key': 'S3CRET2'}", "{'api_key': '[REDACTED]'}"),
        ("{'password': 12345, 'user': 'bob'}", "{'password': [REDACTED], 'user': 'bob'}"),
        ("{'password': 1.5e3}", "{'password': [REDACTED]}"),
        ("{'password': True, 'n': None}", "{'password': [REDACTED], 'n': None}"),
        ("{'private_key': False}", "{'private_key': [REDACTED]}"),
        ("{'password': {'a': 'S3CRET2', 'b': [1]}}", "{'password': [REDACTED]}"),
        (
            "{'cfg': {'client_secret': {'k': ['S3CRET2']}}}",
            "{'cfg': {'client_secret': [REDACTED]}}",
        ),
        ('{"api_key": [\'S3CRET2\', "b"]}', '{"api_key": [REDACTED]}'),
        ("{'password': \"S3CRET2\"}", "{'password': \"[REDACTED]\"}"),
        (
            "upstream failed: {'client_secret': ['a]S3CRET2', 'c'], 'user': 'bob'} retrying",
            "upstream failed: {'client_secret': [REDACTED], 'user': 'bob'} retrying",
        ),
        ("KeyError({'token': 'S3CRET2'})", "KeyError({'token': '[REDACTED]'})"),
        ("KeyError({'token': 12345})", "KeyError({'token': [REDACTED]})"),
        ("KeyError({'access_token': ['S3CRET2']})", "KeyError({'access_token': [REDACTED]})"),
        (
            "ValueError(\"bad: {'api_key': ['S3CRET2']}\")",
            "ValueError(\"bad: {'api_key': [REDACTED]}\")",
        ),
        (
            "{'page_token': 5, 'next_token': ['x'], 'max_tokens': 9}",
            "{'page_token': 5, 'next_token': ['x'], 'max_tokens': 9}",
        ),
        ("{'password': None, 'token': null}", "{'password': None, 'token': null}"),
        ("{'password': NoneS3CRET2}", "{'password': [REDACTED]}"),
    ],
    ids=[
        "list",
        "single-quoted-string",
        "number",
        "float",
        "true",
        "false",
        "nested-dict",
        "deeper-key",
        "mixed-quotes-list",
        "double-quoted-value",
        "repr-in-text-bracket-in-string",
        "exception-token",
        "exception-token-number",
        "exception-access-token-list",
        "repr-inside-exception-string",
        "pagination-keys-untouched",
        "none-stays",
        "nonesuch-masked",
    ],
)
def test_redact_message_python_repr(raw: str, expected: str) -> None:
    """A Python ``repr`` (single-quoted keys) is masked like JSON, value form by value form."""
    assert redact_message(raw) == expected
    assert "S3CRET2" not in redact_message(raw)


def test_redact_message_python_repr_unbalanced_fails_closed() -> None:
    """An unbalanced or stray-quoted ``repr`` value is masked to the end of its line."""
    assert (
        redact_message("{'api_key': ['S3CRET2', 'b'\nnext line")
        == "{'api_key': [REDACTED]\nnext line"
    )
    assert redact_message("{'password': ['it's S3CRET2']} tail") == "{'password': [REDACTED]"


@pytest.mark.parametrize(
    "raw",
    [
        "{'api_key': ['" * 8000,
        "{'password': ['S3CRET2', 'b'\n" * 8000,
        "'api_key': " * 8000,
        "{'a': 'x', " * 8000,
        "'" * 8000,
        "password=['\n" * 8000,
    ],
    ids=[
        "keyed-list-flood",
        "keyed-list-lines",
        "keys-no-value",
        "plain-repr-flood",
        "quotes",
        "apostrophe-lines",
    ],
)
def test_python_repr_flood_is_linear(raw: str) -> None:
    """8,000 single-quoted repeats are redacted in under 3 seconds."""
    import time

    start = time.perf_counter()
    out = redact_message(raw)
    assert time.perf_counter() - start < 3.0
    assert "S3CRET2" not in out


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("password={'v':'}', 'data':'S3CRET2'} tail", "password=[REDACTED] tail"),
        ("password=['a]', 'S3CRET2'] tail", "password=[REDACTED] tail"),
        ("password={'v':'a\\'}', 'data':'S3CRET2'} tail", "password=[REDACTED] tail"),
        ("password={\"v\":\"'\", 'data':'S3CRET2'} tail", "password=[REDACTED] tail"),
    ],
    ids=[
        "brace-in-single-quotes",
        "bracket-in-single-quotes",
        "escaped-single-quote",
        "apostrophe-in-double-quotes",
    ],
)
def test_bracket_value_skips_single_quoted_closers(raw: str, expected: str) -> None:
    """A ``}`` or ``]`` inside a single-quoted string does not end a credential value."""
    assert redact_message(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "a -----BEGIN PRIVATE KEY-----\nAAA\n-----END CERTIFICATE-----\nS3CRET2\n"
            "-----END PRIVATE KEY----- b",
            "a [REDACTED] b",
        ),
        (
            "a -----BEGIN PRIVATE KEY-----\nAAA\n-----END CERTIFICATE-----\nS3CRET2\n"
            "-----end private key----- b",
            "a [REDACTED] b",
        ),
        ("a -----BEGIN PRIVATE KEY-----\nAAA\n-----END CERTIFICATE-----\nS3CRET2", "a [REDACTED]"),
        (
            "-----BEGIN CERTIFICATE-----\nC\n-----END CERTIFICATE----- ok "
            "-----BEGIN RSA PRIVATE KEY-----\nK\n"
            "-----END RSA PRIVATE KEY----- end",
            "[REDACTED] ok [REDACTED] end",
        ),
    ],
    ids=["mismatched-end-inside", "matching-end-any-case", "mismatched-end-only", "two-blocks"],
)
def test_pem_end_must_match_begin_label(raw: str, expected: str) -> None:
    """A PEM block ends only at an END with its own label, or at the end of the text."""
    assert redact_secrets(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "-----BEGIN A-----" * 4000,
        "-----BEGIN PRIVATE KEY-----" + "-----END CERTIFICATE-----" * 4000,
        "-----BEGIN A-----x-----END B-----" * 4000,
    ],
    ids=["begin-flood", "mismatched-end-flood", "begin-mismatched-pairs"],
)
def test_pem_label_match_is_linear(raw: str) -> None:
    """4,000 BEGINs or mismatched ENDs are redacted in under 3 seconds, masked to the end."""
    import time

    start = time.perf_counter()
    out = redact_secrets(raw)
    assert time.perf_counter() - start < 3.0
    assert out == MASK


def test_tuple_under_single_quoted_key_is_masked() -> None:
    """A Python ``repr`` tuple under a credential key is masked to its balanced ``)``."""
    assert redact_secrets("{'api_key': ('a', 'X')} tail") == "{'api_key': " + MASK + "} tail"
    assert redact_secrets('{"password": ("a", "X")}') == '{"password": ' + MASK + "}"
    assert redact_secrets("password=(a, X) ok") == "password=" + MASK + " ok"
    assert redact_secrets("{'api_key': ('a)', 'X')}") == "{'api_key': " + MASK + "}"


def test_nested_tuple_in_list_is_masked() -> None:
    """A tuple nested in a tuple inside a list is masked whole."""
    raw = "[{'api_key': ('a', ('b', 'X'))}, 1]"
    assert redact_secrets(raw) == "[{'api_key': " + MASK + "}, 1]"
    assert redact_message(raw) == "[{'api_key': " + MASK + "}, 1]"


def test_tuple_through_tool_error_is_masked() -> None:
    """``tool_error`` masks a tuple value in the exception message."""
    err = tool_error(ValueError("bad config {'client_secret': ('a', 'X')}"))
    assert str(err) == "bad config {'client_secret': " + MASK + "}"
    assert "X" not in str(err)


def test_unbalanced_paren_masks_to_line_end() -> None:
    """A ``(`` that never balances masks to the end of its line, like ``{`` and ``[``."""
    assert redact_secrets("password=(open\nnext") == "password=" + MASK + "\nnext"
    assert redact_secrets("{'api_key': ('a', 'X'\nok") == "{'api_key': " + MASK + "\nok"


def test_plain_text_parentheses_stay_readable() -> None:
    """Parentheses outside a credential value are left alone."""
    text = "call f(x) (see docs) failed: password is required (retry)"
    assert redact_secrets(text) == text
    assert redact_message(text) == text


@pytest.mark.parametrize("count", [8000, 20000])
def test_keyed_paren_flood_is_fast(count: int) -> None:
    """Thousands of keyed or stray ``(`` stay linear and fail closed."""
    import time

    keyed = ("password=(" + "a" * 10 + "\n") * count
    stray = "api_key=" + "(" * count
    for raw, expected in (
        (keyed, ("password=" + MASK + "\n") * count),
        (stray, "api_key=" + MASK),
    ):
        for redact in (redact_secrets, redact_message):
            began = time.perf_counter()
            assert redact(raw) == expected
            assert time.perf_counter() - began < 3.0


# Single-quoted variants of the house patterns, 20,000 repeats each (product rule: < 3s).
_OWN_SINGLE_QUOTED = [
    "'password': '",
    "'api_key': '",
    "{'client_secret': '",
    "'private_key'='",
    "'token': '",
    "'access_token': '",
    "'refresh_token'= '",
    "authorization: 'bearer ",
    "'authorization': 'token ",
    "\\'password\\': \\'",
    "'password': ['",
    "'api_key': ('",
    "'token': 1",
    "'password' ",
    "-----BEGIN 'A'-----",
]


@pytest.mark.parametrize("unit", _OWN_SINGLE_QUOTED)
def test_own_patterns_single_quoted_flood_is_linear(unit: str) -> None:
    """Every house pattern stays linear on 20,000 single-quoted repeats."""
    raw = unit * 20000
    for redact in (redact_secrets, redact_message):
        start = time.perf_counter()
        redact(raw)
        assert time.perf_counter() - start < 3.0


_UPSTREAM = "upstream failed: {'api_key': ['S3CRETX']} {'client_secret': ('a', 'S3CRETX')}"


@pytest.mark.asyncio
async def test_tools_call_masks_python_repr_list_and_tuple(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real tools/call whose upstream error holds a repr list and tuple returns them masked."""
    import httpx
    from fastmcp import Client

    import espn_mcp.server as srv
    from espn_mcp.client import ESPNClient
    from espn_mcp.server import mcp

    def error_transport(request: httpx.Request) -> httpx.Response:
        raise httpx.RequestError(_UPSTREAM)

    async_client = httpx.AsyncClient(
        transport=httpx.MockTransport(error_transport), base_url="https://site.web.api.espn.com"
    )
    failing = ESPNClient(http_client=async_client, max_retries=0)
    monkeypatch.setattr(srv, "client", failing)
    monkeypatch.setattr("espn_mcp.client.default_client", failing)
    async with Client(mcp) as client:
        res = await client.call_tool(
            "games_get_scoreboard", {"sport": "baseball", "league": "mlb"}, raise_on_error=False
        )
    assert res.is_error is True
    text = " ".join(getattr(c, "text", "") for c in res.content)
    assert "S3CRETX" not in text
    assert "'api_key': " + MASK in text
    assert "'client_secret': " + MASK in text


def test_redact_message_tuple_with_nested_list_is_masked_whole() -> None:
    """A tuple holding a list under a credential key is one mask, not one per bracket."""
    out = redact_message("{'client_secret': ('S3CRETX', ['b'])}")
    assert out == "{'client_secret': " + MASK + "}"
    assert "[REDACTED][REDACTED]" not in out


@pytest.mark.parametrize(
    "raw",
    [
        "(" * 20000,
        "f(x) " * 20000,
        "{'api_key': (" * 20000,
        ("password=(" + "a" * 10 + "\n") * 20000,
        "x" * 300 + "'api_key': " + "(" * 20000,
    ],
    ids=["stray", "prose", "keyed", "keyed-lines", "key-outside-window"],
)
def test_redact_message_paren_flood_is_fast(raw: str) -> None:
    """20,000 ``(`` in ``redact_message`` stay under 3 seconds; ``(`` is not a try."""
    start = time.perf_counter()
    out = redact_message(raw)
    assert time.perf_counter() - start < 3.0
    assert "S3CRETX" not in out


def test_redact_message_paren_is_not_a_bracket_try() -> None:
    """Stray ``(`` never hit the 64-try cap, so the readable tail survives."""
    raw = "f(a) " * 100 + "{'n': 1} tail"
    assert redact_message(raw).endswith("tail")


_PAIRS = {"(": ")", "[": "]", "{": "}"}
_KEY_FORMS = {
    "single": "{'password': %s} tail",
    "double": '{"password": %s} tail',
    "kv": "password=%s tail",
}


@pytest.mark.parametrize("form", sorted(_KEY_FORMS))
@pytest.mark.parametrize("outer", "([{")
@pytest.mark.parametrize("inner", "([{")
def test_two_level_nesting_is_masked_whole(form: str, outer: str, inner: str) -> None:
    """Every ``( [ {`` nested two deep under a credential key is one mask, closers gone."""
    value = outer + "'S3CRETX', " + inner + "'S3CRETX'" + _PAIRS[inner] + _PAIRS[outer]
    template = _KEY_FORMS[form]
    expected = template % MASK
    for redact in (redact_secrets, redact_message):
        out = redact(template % value)
        assert out == expected
        assert out.count(MASK) == 1
        assert "S3CRETX" not in out


# ── Template #80 perf: only look for a credential key before ``(`` after ``:`` or ``=`` ─


def test_prose_parens_skip_the_key_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ``(`` without ``:`` or ``=`` before it never searches the 256-character window."""
    from espn_mcp import errors

    calls: list[int] = []
    real = errors._keyed_before

    def counting(text: str, floor: int, sep: int) -> bool:
        calls.append(sep)
        return real(text, floor, sep)

    monkeypatch.setattr(errors, "_keyed_before", counting)
    text = "f(x) and g(y) see (docs) " * 50
    assert errors.redact_message(text) == text
    assert calls == []
    errors.redact_message("password= ('a', 'b') and x = (1)")
    assert len(calls) == 2


@pytest.mark.parametrize(
    "gap",
    [" " * 300, "\t" * 300, " \t" * 150, " " * 5000, "\t \t" * 2000],
    ids=["300-spaces", "300-tabs", "300-mixed", "5000-spaces", "6000-mixed"],
)
@pytest.mark.parametrize("key", ["password=", "'api_key':", '"client_secret":'])
def test_long_gap_before_keyed_tuple_is_still_masked(key: str, gap: str) -> None:
    """A gap of any length between the key and the tuple still masks the tuple whole."""
    out = redact_message(f"{key}{gap}('s3cret', ['b']) tail")
    assert "s3cret" not in out and "'b'" not in out
    assert out.endswith(MASK + " tail")


@pytest.mark.parametrize(
    "raw",
    [
        "password=(x\n" * 20000,
        "f(x) and g(y) see (docs) " * 20000,
        "(   " * 100000,
        "( \t " * 100000,
        (" " * 300 + "(") * 1000,
        ("=" + " " * 300 + "(") * 1000,
    ],
    ids=["keyed-flood-20k", "prose-20k", "spaced-100k", "mixed-100k", "long-gap", "eq-long-gap"],
)
def test_redact_message_paren_floods_with_gaps_stay_fast(raw: str) -> None:
    """Parenthesis floods, with or without whitespace gaps, stay under 3 s (product rule)."""
    began = time.perf_counter()
    redact_message(raw)
    assert time.perf_counter() - began < 3.0
