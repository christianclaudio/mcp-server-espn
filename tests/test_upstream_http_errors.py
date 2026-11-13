"""Upstream status and bounded message on ESPN HTTP errors.

The values below are obvious fakes. They are not credentials.
"""

from __future__ import annotations

import sys
from typing import Any

import httpx
import pytest

from espn_mcp.client import (
    _UPSTREAM_ELLIPSIS,
    _UPSTREAM_MESSAGE_MAX_CHARS,
    ESPNClient,
    _recorded_secret_pattern,
    _sub_recorded_secrets,
)
from espn_mcp.errors import (
    ESPNConnectionError,
    ESPNNotFoundError,
    ESPNRateLimitError,
    ESPNValidationError,
)

_BASE = "https://site.web.api.espn.com"
_PATH = "apis/site/v2/sports/nba/scoreboard"
_PLAIN = "line one\n\nline  two"
_PLAIN_DETAIL = "line one line two"
_JSON_MESSAGE = "Game id missing"
_JSON_ERROR = "Unknown league"
_JSON_ERROR_OBJECT = "bad season"
_HIDDEN_CODE = "HIDDEN-CODE-991"
_TAIL = "TAIL-SHOULD-NOT-APPEAR"
_LONG = ("N" * (_UPSTREAM_MESSAGE_MAX_CHARS - len(_UPSTREAM_ELLIPSIS))) + _TAIL

# Obvious fakes. Not cookies, tokens, or account values.
_FAKE_S2 = "fake-espn-s2-value-not-a-secret"
_FAKE_SWID = "{FAKE-SWID-0000-0000-0000-000000000000}"
_FAKE_QUERY_VALUE = "fake-query-trace-value"
_FAKE_AUTH = "Bearer fake-auth-value-not-a-secret"
_FAKE_USERINFO = "fake-userinfo-password-value"

_STATUSES = [
    pytest.param(404, ESPNNotFoundError, id="404"),
    pytest.param(429, ESPNRateLimitError, id="429"),
    pytest.param(400, ESPNValidationError, id="4xx"),
    pytest.param(503, ESPNConnectionError, id="5xx"),
]


def _cookie_value(header: str, name: str) -> str:
    """Return one cookie value from a Cookie header. Names are case-insensitive."""
    for part in header.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        if key.strip().lower() == name.lower():
            return value.strip().strip('"')
    return ""


def _prefix(status: int) -> str:
    if status == 404:
        return f"HTTP 404: ESPN resource at '{_PATH}' not found."
    if status == 429:
        return "HTTP 429: Rate limit exceeded after retries."
    if status >= 500:
        return f"HTTP {status}: Upstream server error."
    return f"HTTP {status}: ESPN rejected the request for '{_PATH}'."


def _detail(message: str, status: int) -> str:
    base = _prefix(status)
    assert message.startswith(base)
    rest = message[len(base) :]
    if not rest:
        return ""
    assert rest.startswith(" Upstream: "), message
    return rest[len(" Upstream: ") :]


def _body(kind: str) -> httpx.Response:
    status = 0
    if kind == "message":
        return httpx.Response(status, json={"message": _JSON_MESSAGE})
    if kind == "error":
        return httpx.Response(status, json={"error": _JSON_ERROR})
    if kind == "error_object":
        return httpx.Response(
            status,
            json={"error": {"message": _JSON_ERROR_OBJECT, "code": _HIDDEN_CODE}},
        )
    if kind == "text":
        return httpx.Response(status, text=_PLAIN)
    if kind == "empty":
        return httpx.Response(status)
    if kind == "long":
        return httpx.Response(status, text=_LONG)
    raise AssertionError(kind)


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retry delays must not wait on the clock."""

    async def _sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr("espn_mcp.client.asyncio.sleep", _sleep)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,exc_type", _STATUSES)
@pytest.mark.parametrize(
    "kind",
    ["message", "error", "error_object", "text", "empty", "long"],
)
async def test_upstream_http_error_includes_bounded_message(
    status: int,
    exc_type: type[BaseException],
    kind: str,
    no_sleep: None,
) -> None:
    """Each HTTP error class carries status plus a bounded upstream message."""
    assert _UPSTREAM_MESSAGE_MAX_CHARS == 300
    calls = {"n": 0}
    template = _body(kind)

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(status, content=template.content, headers=template.headers)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=_BASE) as http:
        client = ESPNClient(max_retries=1, http_client=http)
        with pytest.raises(exc_type) as exc_info:
            await client.request("GET", _PATH)

    message = str(exc_info.value)
    detail = _detail(message, status)
    assert str(status) in message
    expected_calls = 2 if status in (429, 503) else 1
    assert calls["n"] == expected_calls
    if kind == "message":
        assert detail == _JSON_MESSAGE
    elif kind == "error":
        assert detail == _JSON_ERROR
    elif kind == "error_object":
        assert detail == _JSON_ERROR_OBJECT
        assert _HIDDEN_CODE not in message
    elif kind == "text":
        assert detail == _PLAIN_DETAIL
    elif kind == "empty":
        assert detail == ""
        assert message == _prefix(status)
    else:
        assert len(detail) == _UPSTREAM_MESSAGE_MAX_CHARS
        assert detail.endswith(_UPSTREAM_ELLIPSIS)
        assert _TAIL not in message
        assert detail == ("N" * (_UPSTREAM_MESSAGE_MAX_CHARS - len(_UPSTREAM_ELLIPSIS))) + (
            _UPSTREAM_ELLIPSIS
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("status,exc_type", _STATUSES)
async def test_upstream_error_redacts_echoed_cookies_and_query(
    status: int,
    exc_type: type[BaseException],
    no_sleep: None,
) -> None:
    """A body that echoes cookies or the query must not leak those values."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        query = request.url.query.decode()
        cookie_header = request.headers.get("cookie", "")
        authorization = request.headers.get("authorization", "")
        s2 = _cookie_value(cookie_header, "espn_s2")
        swid = _cookie_value(cookie_header, "SWID")
        captured.update(
            query=query,
            cookie=cookie_header,
            authorization=authorization,
            s2=s2,
            swid=swid,
        )
        echoed = "\n".join(
            [
                "upstream rejected the fixture",
                f"espn_s2={s2}",
                f"SWID={swid}",
                f"Cookie: {cookie_header}",
                f"Authorization: {authorization}",
                f"Set-Cookie: espn_s2={s2}",
                f"see https://site.api.espn.com:443/apis/site/v2/scoreboard?{query}",
                (f"https://user:{_FAKE_USERINFO}@site.api.espn.com/private?x={_FAKE_QUERY_VALUE}"),
            ]
        )
        return httpx.Response(
            status,
            json={"message": echoed},
            headers={"Set-Cookie": f"espn_s2={s2}; Path=/"},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url=_BASE,
        cookies={"espn_s2": _FAKE_S2, "SWID": _FAKE_SWID},
        headers={"Authorization": _FAKE_AUTH},
    ) as http:
        client = ESPNClient(max_retries=0, http_client=http)
        with pytest.raises(exc_type) as exc_info:
            await client.request("GET", _PATH, params={"trace": _FAKE_QUERY_VALUE})

    message = str(exc_info.value)
    assert captured["s2"] == _FAKE_S2
    assert captured["swid"] == _FAKE_SWID
    assert _FAKE_QUERY_VALUE in captured["query"]
    assert captured["cookie"]
    assert captured["authorization"] == _FAKE_AUTH
    for secret in (
        _FAKE_S2,
        _FAKE_SWID,
        _FAKE_QUERY_VALUE,
        captured["query"],
        captured["cookie"],
        _FAKE_AUTH,
        _FAKE_USERINFO,
        "fake-auth-value-not-a-secret",
    ):
        assert secret not in message
    assert "upstream rejected the fixture" in message
    assert "https://site.api.espn.com:443/apis/site/v2/scoreboard" in message
    assert "https://site.api.espn.com/private" in message
    assert "?" not in message


# Obvious fake. The slash is percent-encoded as %2F or %2f.
_FAKE_SLASH = "abc/def123"


@pytest.mark.asyncio
async def test_scrub_matches_percent_hex_ignoring_case() -> None:
    """A slash in a cookie value is scrubbed raw and as ``%2F`` or ``%2f``."""
    lower = "abc%2fdef123"
    upper = "abc%2Fdef123"

    def handler(_request: httpx.Request) -> httpx.Response:
        echoed = f"rejected raw {_FAKE_SLASH} lower {lower} upper {upper}"
        return httpx.Response(400, json={"message": echoed})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url=_BASE,
        cookies={"espn_s2": _FAKE_SLASH},
    ) as http:
        client = ESPNClient(max_retries=0, http_client=http)
        with pytest.raises(ESPNValidationError) as exc_info:
            await client.request("GET", _PATH)

    detail = _detail(str(exc_info.value), 400)
    assert _FAKE_SLASH not in detail
    assert lower not in detail
    assert upper not in detail
    assert "rejected" in detail


_JSON_HYPHEN = "abc-def123"


@pytest.mark.asyncio
async def test_scrub_decoded_json_unicode_escape_value() -> None:
    """A recorded value hidden as a JSON ``\\u`` escape is scrubbed after decode."""
    body = '{"note":"abc\\u002ddef123"}'

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, content=body.encode(), headers={"Content-Type": "application/json"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=_BASE) as http:
        client = ESPNClient(max_retries=0, http_client=http)
        with pytest.raises(ESPNValidationError) as exc_info:
            await client.request("GET", _PATH, params={"trace": _JSON_HYPHEN})

    detail = _detail(str(exc_info.value), 400)
    assert _JSON_HYPHEN not in detail


@pytest.mark.asyncio
async def test_scrub_decoded_json_unicode_escape_key() -> None:
    """A recorded value used as a JSON key with a ``\\u`` escape is scrubbed."""
    body = '{"abc\\u002ddef123":"ok"}'

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, content=body.encode(), headers={"Content-Type": "application/json"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=_BASE) as http:
        client = ESPNClient(max_retries=0, http_client=http)
        with pytest.raises(ESPNValidationError) as exc_info:
            await client.request("GET", _PATH, params={"trace": _JSON_HYPHEN})

    detail = _detail(str(exc_info.value), 400)
    assert _JSON_HYPHEN not in detail


def test_recorded_secret_pattern_is_absent_without_values() -> None:
    """No recorded exchange value means the scrub leaves the text unchanged."""
    request = httpx.Request(
        "GET",
        "https://example.test/x",
        headers={"Accept": "json", "Host": "example.test"},
    )
    response = httpx.Response(400, text="plain text", request=request)
    assert _recorded_secret_pattern(response) is None
    assert _sub_recorded_secrets("plain text", None) == "plain text"


_USERINFO_URL = "https://user@corp:secret@example.net/path"


@pytest.mark.asyncio
async def test_url_userinfo_is_stripped_through_the_last_at() -> None:
    """user@corp:secret@host keeps the host and drops every credential."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"message": f"see {_USERINFO_URL}"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=_BASE) as http:
        client = ESPNClient(max_retries=0, http_client=http)
        with pytest.raises(ESPNValidationError) as exc_info:
            await client.request("GET", _PATH)

    detail = _detail(str(exc_info.value), 400)
    assert "https://example.net/path" in detail
    assert "user" not in detail
    assert "corp" not in detail
    assert "secret" not in detail


_FAKE_SIGNATURE = "fake-signing-header-value"


@pytest.mark.asyncio
async def test_signed_request_header_value_is_scrubbed() -> None:
    """A caller-supplied signing header echoed by upstream is not in the error."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"message": f"sig {_FAKE_SIGNATURE}"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url=_BASE,
        headers={"X-Signing-Token": _FAKE_SIGNATURE},
    ) as http:
        client = ESPNClient(max_retries=0, http_client=http)
        with pytest.raises(ESPNValidationError) as exc_info:
            await client.request("GET", _PATH)

    detail = _detail(str(exc_info.value), 400)
    assert _FAKE_SIGNATURE not in detail
    assert "sig" in detail


_FAKE_INT_SECRET = "fake-int-limit-secret-not-real"


@pytest.mark.asyncio
async def test_oversized_integer_body_is_capped_http_error() -> None:
    """An integer past the digit limit stays an HTTP error with a redacted cap."""
    body = '{"note":"password=' + _FAKE_INT_SECRET + '","error":' + ("1" * 5000) + "}"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            content=body.encode(),
            headers={"Content-Type": "application/json"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=_BASE) as http:
        client = ESPNClient(max_retries=0, http_client=http)
        with pytest.raises(ESPNValidationError) as exc_info:
            await client.request("GET", _PATH)

    message = str(exc_info.value)
    assert type(exc_info.value) is ESPNValidationError
    detail = _detail(message, 400)
    assert len(detail) <= _UPSTREAM_MESSAGE_MAX_CHARS
    assert _FAKE_INT_SECRET not in detail


@pytest.mark.asyncio
@pytest.mark.parametrize("status,exc_type", _STATUSES)
async def test_empty_body_does_not_copy_query_or_cookies(
    status: int,
    exc_type: type[BaseException],
    no_sleep: None,
) -> None:
    """Request cookies and the query stay out of the error when the body is empty."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url=_BASE,
        cookies={"espn_s2": _FAKE_S2, "SWID": _FAKE_SWID},
        headers={"Authorization": _FAKE_AUTH},
    ) as http:
        client = ESPNClient(max_retries=0, http_client=http)
        with pytest.raises(exc_type) as exc_info:
            await client.request("GET", _PATH, params={"trace": _FAKE_QUERY_VALUE})

    message = str(exc_info.value)
    assert message == _prefix(status)
    assert _FAKE_S2 not in message
    assert _FAKE_SWID not in message
    assert _FAKE_QUERY_VALUE not in message
    assert "fake-auth-value-not-a-secret" not in message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "present", "absent"),
    [
        ({"content": b""}, None, ()),
        ({"content": b" \n\t"}, None, ()),
        ({"content": b"\xff\xfe"}, None, ()),
        (
            {
                "content": b"hello",
                "headers": {"Content-Type": "text/plain; charset=not-a-charset"},
            },
            None,
            ("hello",),
        ),
        ({"json": {"message": "   \n  "}}, None, ()),
        (
            {"json": {"message": "preferred", "error": "other-field"}},
            "preferred",
            ("other-field",),
        ),
        ({"json": {"message": None, "error": "from-error-field"}}, "from-error-field", ()),
        ({"json": {"error": {"code": "ONLY-CODE"}}}, "ONLY-CODE", ()),
        ({"json": {"status": "bad-shape"}}, "bad-shape", ()),
        (
            {
                "content": b'{"message": "bare-json"}',
                "headers": {"Content-Type": "text/plain"},
            },
            "bare-json",
            (),
        ),
        (
            {"content": b"not-json", "headers": {"Content-Type": "application/json"}},
            "not-json",
            (),
        ),
        (
            {"content": b"[1, 2, 3]", "headers": {"Content-Type": "application/json"}},
            "[1, 2, 3]",
            (),
        ),
    ],
)
async def test_upstream_message_json_and_undecodable_shapes(
    kwargs: dict[str, Any],
    present: str | None,
    absent: tuple[str, ...],
    no_sleep: None,
) -> None:
    """JSON field choice, raw-text fallback, and undecodable bodies."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, **kwargs)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=_BASE) as http:
        client = ESPNClient(max_retries=0, http_client=http)
        with pytest.raises(ESPNValidationError) as exc_info:
            await client.request("GET", _PATH)

    message = str(exc_info.value)
    if present is None:
        assert message == _prefix(400)
    else:
        assert present in message
    for item in absent:
        assert item not in message


@pytest.mark.asyncio
async def test_quoted_url_query_value_is_omitted() -> None:
    """A quoted query on a URL in the body is removed with the query."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"message": 'see https://example.net/path?access_token="private-value"'},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=_BASE) as http:
        client = ESPNClient(max_retries=0, http_client=http)
        with pytest.raises(ESPNValidationError) as exc_info:
            await client.request("GET", _PATH)

    message = str(exc_info.value)
    assert "private-value" not in message
    assert "?access_token=" not in message
    assert "https://example.net/path" in message


def _deep_object() -> str:
    """A valid object deep enough that the redaction walk overflows.

    Python 3.11's parser raises at about 1,000 levels, and 900 still parses
    there while the walk overflows. 3.12 and newer parse much deeper, and the
    walk does not overflow until past about 1,000 levels, so those use 2,000.
    """
    depth = 900 if sys.version_info < (3, 12) else 2_000
    return '{"a":' * depth + "1" + "}" * depth


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        "[" * 200_000,
        _deep_object(),
    ],
    ids=["unclosed-arrays", "nested-object"],
)
async def test_deeply_nested_upstream_body_is_bounded(body: str) -> None:
    """Deep JSON still raises the HTTP error, with the raw text inside the cap."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            content=body.encode(),
            headers={"Content-Type": "application/json"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=_BASE) as http:
        client = ESPNClient(max_retries=0, http_client=http)
        with pytest.raises(ESPNValidationError) as exc_info:
            await client.request("GET", _PATH)

    message = str(exc_info.value)
    assert type(exc_info.value) is ESPNValidationError
    detail = _detail(message, 400)
    assert detail
    assert len(detail) <= _UPSTREAM_MESSAGE_MAX_CHARS


# Obvious fake. redact_secrets masks the value of password=.
_FAKE_SECRET = "fake-upstream-secret-value-not-real"


def _secret_across_cut(*, nested: bool) -> str:
    """Place ``password=<secret>`` so the value straddles the truncation point.

    The nested form opens arrays, puts the secret in a string, then continues
    with enough ``[`` that ``json.loads`` raises ``RecursionError``. The plain
    form is the same value with no JSON around it.
    """
    key = "password="
    inside = 8
    value_at = _UPSTREAM_MESSAGE_MAX_CHARS - len(_UPSTREAM_ELLIPSIS) - inside
    if not nested:
        pad = value_at - len(key)
        assert pad > 0
        return ("n" * pad) + key + _FAKE_SECRET
    brackets = value_at - len(key) - 1
    assert brackets > 0
    return "[" * brackets + '"' + key + _FAKE_SECRET + '"' + "," + "[" * 200_000


@pytest.mark.asyncio
@pytest.mark.parametrize("nested", [False, True], ids=["plain", "parser-overflow"])
async def test_secret_crossing_cut_is_redacted_before_truncation(nested: bool) -> None:
    """A secret that straddles the cap is masked on the full text, then cut."""
    body = _secret_across_cut(nested=nested)
    content_type = "application/json" if nested else "text/plain"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            content=body.encode(),
            headers={"Content-Type": content_type},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=_BASE) as http:
        client = ESPNClient(max_retries=0, http_client=http)
        with pytest.raises(ESPNValidationError) as exc_info:
            await client.request("GET", _PATH)

    message = str(exc_info.value)
    assert type(exc_info.value) is ESPNValidationError
    detail = _detail(message, 400)
    assert len(detail) <= _UPSTREAM_MESSAGE_MAX_CHARS
    for length in range(4, len(_FAKE_SECRET) + 1):
        assert _FAKE_SECRET[:length] not in detail
