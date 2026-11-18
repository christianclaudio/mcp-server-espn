"""Unit tests for the enterprise OpenAPI and parameter drift detection engine."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from tests.fake_network import FakeNetwork, answer

scripts_dir = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(scripts_dir))

from check_openapi_drift import (  # noqa: E402
    DEFAULT_TEMPLATE_SPEC,
    SpecFetchError,
    _registered_tools,
    fetch_spec_text,
    fetch_url_status,
    is_parameter_deprecated,
    load_route_table,
    load_site_routes,
    main,
    normalize_path,
    parse_spec,
    parse_wadl_get_routes,
    run_drift_check,
)


def test_normalize_path() -> None:
    assert normalize_path("items") == "/items"
    assert normalize_path("/items/") == "/items"
    assert normalize_path("/items/{id}") == "/items/{}"
    assert normalize_path("/v2/members/{memberId}/files") == "/v2/members/{}/files"
    assert normalize_path("/items?limit=50") == "/items"


def test_is_parameter_deprecated() -> None:
    assert is_parameter_deprecated({"name": "search", "deprecated": True}) is True
    assert (
        is_parameter_deprecated(
            {"name": "search", "description": "**[Deprecated]** Use email instead"}
        )
        is True
    )
    assert (
        is_parameter_deprecated(
            {"name": "search", "description": "This reaches end of support soon"}
        )
        is True
    )
    assert (
        is_parameter_deprecated({"name": "email", "description": "Filter by user email"}) is False
    )


def test_parse_spec() -> None:
    spec = {
        "paths": {
            "/v2/members": {
                "get": {
                    "parameters": [
                        {"name": "search", "in": "query", "deprecated": True},
                        {"name": "email", "in": "query", "required": True},
                    ]
                }
            }
        }
    }
    endpoints = parse_spec(spec)
    key = ("GET", "/v2/members")
    assert key in endpoints
    ep = endpoints[key]
    assert "search" in ep.deprecated_params
    assert "email" in ep.required_params


def test_run_drift_check_clean(tmp_path: Path) -> None:
    src_dir = Path(__file__).parent.parent / "src"
    code, lines = run_drift_check(DEFAULT_TEMPLATE_SPEC, src_dir, strict=False)
    assert code == 0
    assert any("No breaking drift" in line for line in lines)


def test_run_drift_check_detects_deprecated_param(tmp_path: Path) -> None:
    # Create mock client file that passes a deprecated parameter "search"
    mock_src = tmp_path / "mock_client.py"
    mock_src.write_text(
        'client.request("GET", "items", params={"search": "query", "limit": 10})\n',
        encoding="utf-8",
    )
    code, lines = run_drift_check(DEFAULT_TEMPLATE_SPEC, tmp_path, strict=False)
    assert code == 0  # Warning in non-strict
    assert any("DEPRECATED PARAMETER IN USE: 'search'" in line for line in lines)

    # In strict mode, it must fail
    code_strict, lines_strict = run_drift_check(DEFAULT_TEMPLATE_SPEC, tmp_path, strict=True)
    assert code_strict == 1
    assert any("DEPRECATED PARAMETER IN USE: 'search'" in line for line in lines_strict)


def test_run_drift_check_detects_phantom_route(tmp_path: Path) -> None:
    mock_src = tmp_path / "mock_client.py"
    mock_src.write_text(
        'client.request("GET", "unknown_route")\n',
        encoding="utf-8",
    )
    code, lines = run_drift_check(DEFAULT_TEMPLATE_SPEC, tmp_path, strict=False)
    assert code == 1
    assert any("PHANTOM ROUTE" in line for line in lines)


def test_main_cli_default() -> None:
    with patch("sys.argv", ["check_openapi_drift.py"]):
        assert main() == 0


def test_main_cli_spec_file(tmp_path: Path) -> None:
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(DEFAULT_TEMPLATE_SPEC), encoding="utf-8")
    with patch("sys.argv", ["check_openapi_drift.py", "--spec-file", str(spec_file)]):
        assert main() == 0

    # Non-existent file
    missing = str(tmp_path / "missing.json")
    with patch("sys.argv", ["check_openapi_drift.py", "--spec-file", missing]):
        assert main() == 2


SPEC_HOST = "specs.vendor.test"
SPEC_URL = f"https://{SPEC_HOST}/openapi.json"
PUBLIC_IP = "93.184.215.14"


def _spec_network(**kwargs: Any) -> FakeNetwork:
    return FakeNetwork(body=kwargs.pop("body", DEFAULT_TEMPLATE_SPEC), **kwargs)


def _run(url: str) -> int:
    with patch("sys.argv", ["check_openapi_drift.py", "--spec-url", url]):
        return main()


def test_main_cli_spec_url_resolves_once_and_connects_to_the_checked_ip() -> None:
    """The fetch connects to the address the SSRF check saw; it never looks the host up again.

    The hostname still goes in the Host header and is the TLS server name, so SNI and
    certificate verification use it.
    """
    net = _spec_network()
    with net.patches(), patch("socket.getaddrinfo", return_value=answer(PUBLIC_IP)) as dns:
        assert _run(SPEC_URL) == 0
    assert dns.call_count == 1
    assert dns.call_args.args[0] == SPEC_HOST
    assert net.connects == [(PUBLIC_IP, 443)]
    assert net.tls_server_names == [SPEC_HOST]
    assert f"\r\nHost: {SPEC_HOST}\r\n".encode() in net.sent
    assert net.sent.startswith(b"GET /openapi.json HTTP/1.1\r\n")


def test_main_cli_spec_url_ignores_environment_proxies(monkeypatch: pytest.MonkeyPatch) -> None:
    """httpx ignores env proxies when a transport is passed: the fetch connects directly."""
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.vendor.test:3128")
    monkeypatch.setenv("ALL_PROXY", "http://proxy.vendor.test:3128")
    net = _spec_network()
    with net.patches(), patch("socket.getaddrinfo", return_value=answer(PUBLIC_IP)) as dns:
        assert _run(SPEC_URL) == 0
    assert dns.call_count == 1
    assert net.connects == [(PUBLIC_IP, 443)]
    assert b"CONNECT " not in net.sent


def test_main_cli_spec_url_does_not_follow_redirects(capsys: pytest.CaptureFixture[str]) -> None:
    """A 3xx is an error, not a hop to a target the SSRF check never saw."""
    net = _spec_network(status=302)
    net.response = net.response.replace(
        b"Content-Type:", b"Location: https://169.254.169.254/latest\r\nContent-Type:", 1
    )
    with net.patches(), patch("socket.getaddrinfo", return_value=answer(PUBLIC_IP)):
        assert _run(SPEC_URL) == 2
    assert net.attempts == [(PUBLIC_IP, 443)]
    assert "Redirect response '302" in capsys.readouterr().err


@pytest.mark.parametrize("blocked", ["10.0.0.5", "198.18.0.1", "169.254.169.254"])
def test_main_cli_spec_url_refuses_non_global_addresses(
    blocked: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """A name resolving to, or a URL naming, a non-global address is refused unconnected."""
    net = _spec_network()
    with net.patches(), patch("socket.getaddrinfo", return_value=answer(blocked)) as dns:
        assert _run(SPEC_URL) == 2
        dns.assert_called_once()
        err = capsys.readouterr().err
        assert "refused to fetch spec URL" in err
        assert blocked in err

        assert _run(f"https://{blocked}/openapi.json") == 2
        assert "refused to fetch spec URL" in capsys.readouterr().err
        dns.assert_called_once()
    assert net.attempts == []


def test_main_cli_spec_url_refuses_metadata_and_placeholder_hosts(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Plain HTTP is refused up front; an example.com placeholder has no checked address."""
    net = _spec_network()
    with net.patches(), patch("socket.getaddrinfo", return_value=answer(PUBLIC_IP)) as dns:
        assert _run("http://169.254.169.254/latest/meta-data") == 2
        assert "refused to fetch spec URL" in capsys.readouterr().err
        assert _run("https://api.example.com/openapi.json") == 2
        err = capsys.readouterr().err
        assert "refused to fetch spec URL" in err
        assert "no checked address" in err
    dns.assert_not_called()
    assert net.attempts == []


def test_main_cli_spec_url_errors_are_visible(capsys: pytest.CaptureFixture[str]) -> None:
    with (
        _spec_network(status=503).patches(),
        patch("socket.getaddrinfo", return_value=answer(PUBLIC_IP)),
    ):
        assert _run(SPEC_URL) == 2
    assert "Error fetching spec from URL: Server error '503" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("content_type", "body"),
    [
        (b"text/html", b"<html><body>Service temporarily unavailable</body></html>"),
        (b"application/yaml", b"openapi: 3.1.0\ninfo:\n  title: ESPN\npaths: {}\n"),
        (b"application/json", b"\xff\xfe\x00not json"),
    ],
    ids=["html", "yaml", "undecodable"],
)
def test_main_cli_spec_url_non_json_body_is_not_a_refusal(
    content_type: bytes, body: bytes, capsys: pytest.CaptureFixture[str]
) -> None:
    """A 200 whose body isn't JSON exits 2 as non-JSON, naming the URL and status only."""
    net = _spec_network()
    net.response = (
        b"HTTP/1.1 200 OK\r\nContent-Type: %s\r\nContent-Length: %d\r\n\r\n"
        % (content_type, len(body))
    ) + body
    with net.patches(), patch("socket.getaddrinfo", return_value=answer(PUBLIC_IP)):
        assert _run(SPEC_URL) == 2
    assert net.connects == [(PUBLIC_IP, 443)]
    err = capsys.readouterr().err
    assert f"spec URL returned non-JSON: {SPEC_URL} (HTTP 200)" in err
    assert "refused to fetch spec URL" not in err
    assert "Error fetching spec from URL" not in err
    # Nothing from the body is echoed.
    assert "Service temporarily" not in err
    assert "openapi:" not in err
    assert "not json" not in err


_V2_WADL = """<?xml version="1.0"?>
<application xmlns="http://wadl.dev.java.net/2009/02">
  <resources base="http://sports.core.api.espn.com/v2/">
    <resource path="/sports/{sport}/leagues/{league}">
      <method name="GET" id="getLeague"/>
      <resource path="/venues">
        <method name="GET" id="getVenues"/>
      </resource>
      <resource path="notes">
        <method name="GET" id="getNotes"/>
      </resource>
    </resource>
    <resource path="extra">
      <method name="GET" id="getExtra"/>
    </resource>
  </resources>
</application>
"""

_V3_WADL = """<?xml version="1.0"?>
<application xmlns="http://wadl.dev.java.net/2009/02">
  <resources base="http://sports.core.api.espn.com/v3/">
    <resource path="/bets/featured">
      <method name="GET" id="getBetFeatured"/>
    </resource>
    <resource path="/brand/new">
      <method name="GET" id="getNew"/>
    </resource>
  </resources>
</application>
"""

_SITE = (
    "GET /apis/site/v2/sports/{}/{}/scoreboard  "
    "# covered: games_get_scoreboard "
    "https://site.web.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard?limit=1\n"
    "GET /apis/site/v2/sports/{}/{}/injuries  # excluded: not checked\n"
)


def _write_routes(tmp_path: Path, snapshot: str, site: str = _SITE) -> list[str]:
    snap = tmp_path / "routes.tsv"
    site_path = tmp_path / "site.txt"
    snap.write_text(snapshot, encoding="utf-8")
    site_path.write_text(site, encoding="utf-8")
    return [
        "--fail-on-new-uncovered",
        "--routes-snapshot",
        str(snap),
        "--site-routes",
        str(site_path),
    ]


def _live_paths() -> set[str]:
    return parse_wadl_get_routes(_V2_WADL, "v2") | parse_wadl_get_routes(_V3_WADL, "v3")


def _table(paths: set[str], covered: dict[str, str] | None = None) -> str:
    lines = []
    for path in sorted(paths):
        if covered and path in covered:
            lines.append(f"GET {path}  # covered: {covered[path]}")
        else:
            lines.append(f"GET {path}  # gap: held for this fixture")
    return "\n".join(lines) + "\n"


def test_fail_on_new_uncovered_reports_a_new_route(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    live = _live_paths()
    omitted = "/v3/brand/new"
    assert omitted in live

    async def ok(_url: str) -> str:
        return "200"

    with (
        patch(
            "sys.argv",
            ["check_openapi_drift.py", *_write_routes(tmp_path, _table(live - {omitted}))],
        ),
        patch("check_openapi_drift.fetch_spec_text", _fetch_pair),
        patch("check_openapi_drift.fetch_url_status", ok),
    ):
        assert main() == 1
    out = capsys.readouterr().out
    assert "❌ New uncovered ESPN routes: 1" in out
    assert "GET /v3/brand/new" in out


async def _fetch_pair(url: str) -> str:
    if "/v2/" in url:
        return _V2_WADL
    if "/v3/" in url:
        return _V3_WADL
    raise AssertionError(url)


def test_baselined_routes_pass(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    async def ok(_url: str) -> str:
        return "200"

    covered = {"/v2/sports/{}/leagues/{}": "games_get_scoreboard"}
    with (
        patch(
            "sys.argv",
            ["check_openapi_drift.py", *_write_routes(tmp_path, _table(_live_paths(), covered))],
        ),
        patch("check_openapi_drift.fetch_spec_text", _fetch_pair),
        patch("check_openapi_drift.fetch_url_status", ok),
    ):
        assert main() == 0
    out = capsys.readouterr().out
    assert "No breaking drift" in out
    assert "New uncovered" not in out


def test_vanished_baseline_route_is_a_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    async def ok(_url: str) -> str:
        return "200"

    text = _table(_live_paths()) + "GET /v2/retired  # gap: removed upstream\n"
    with (
        patch("sys.argv", ["check_openapi_drift.py", *_write_routes(tmp_path, text)]),
        patch("check_openapi_drift.fetch_spec_text", _fetch_pair),
        patch("check_openapi_drift.fetch_url_status", ok),
    ):
        assert main() == 0
    assert "⚠️ Baseline route vanished: GET /v2/retired" in capsys.readouterr().out


def test_no_flag_keeps_todays_output(capsys: pytest.CaptureFixture[str]) -> None:
    with patch("sys.argv", ["check_openapi_drift.py"]):
        assert main() == 0
    out = capsys.readouterr().out
    assert "No breaking drift or path mismatches detected." in out
    assert "New uncovered" not in out


def test_bad_tag_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with patch(
        "sys.argv",
        ["check_openapi_drift.py", *_write_routes(tmp_path, "GET /v2/only  # bogus: no\n")],
    ):
        assert main() == 2
    err = capsys.readouterr().err
    assert "bad tag" in err
    assert "New uncovered" not in err


def test_mapped_tool_that_is_gone_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    async def ok(_url: str) -> str:
        return "200"

    covered = {"/v2/sports/{}/leagues/{}": "games_not_a_tool"}
    with (
        patch(
            "sys.argv",
            ["check_openapi_drift.py", *_write_routes(tmp_path, _table(_live_paths(), covered))],
        ),
        patch("check_openapi_drift.fetch_spec_text", _fetch_pair),
        patch("check_openapi_drift.fetch_url_status", ok),
    ):
        assert main() == 1
    out = capsys.readouterr().out
    assert "Mapped tool is not registered: games_not_a_tool" in out
    assert "New uncovered" not in out


def test_wadl_fetch_failure_exits_2_without_new_routes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    async def boom(_url: str) -> str:
        raise SpecFetchError("connection error")

    with (
        patch(
            "sys.argv",
            ["check_openapi_drift.py", *_write_routes(tmp_path, _table(_live_paths()))],
        ),
        patch("check_openapi_drift.fetch_spec_text", boom),
    ):
        assert main() == 2
    out = capsys.readouterr().out
    assert "::error::WADL fetch/parse failed: connection error" in out
    assert "New uncovered" not in out


def test_malformed_wadl_exits_2_without_new_routes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    async def bad(_url: str) -> str:
        return "<not xml"

    with (
        patch(
            "sys.argv",
            ["check_openapi_drift.py", *_write_routes(tmp_path, _table(_live_paths()))],
        ),
        patch("check_openapi_drift.fetch_spec_text", bad),
    ):
        assert main() == 2
    out = capsys.readouterr().out
    assert "::error::WADL fetch/parse failed: malformed XML" in out
    assert "New uncovered" not in out


def test_site_retry_then_success_passes(tmp_path: Path) -> None:
    calls: list[str] = []

    async def status(url: str) -> str:
        calls.append(url)
        if url.endswith("/injuries"):
            raise AssertionError("excluded route was fetched")
        return "404" if len(calls) == 1 else "200"

    with (
        patch(
            "sys.argv",
            ["check_openapi_drift.py", *_write_routes(tmp_path, _table(_live_paths()))],
        ),
        patch("check_openapi_drift.fetch_spec_text", _fetch_pair),
        patch("check_openapi_drift.fetch_url_status", status),
    ):
        assert main() == 0
    assert not any(url.endswith("/injuries") for url in calls)
    assert len(calls) == 2


def test_site_route_dead_after_retry_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    async def status(_url: str) -> str:
        return "404"

    with (
        patch(
            "sys.argv",
            ["check_openapi_drift.py", *_write_routes(tmp_path, _table(_live_paths()))],
        ),
        patch("check_openapi_drift.fetch_spec_text", _fetch_pair),
        patch("check_openapi_drift.fetch_url_status", status),
    ):
        assert main() == 1
    out = capsys.readouterr().out
    assert "❌ Dead site route: /apis/site/v2/sports/{}/{}/scoreboard (404)" in out


def test_site_host_outage_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    async def status(_url: str) -> str:
        return "connection"

    with (
        patch(
            "sys.argv",
            ["check_openapi_drift.py", *_write_routes(tmp_path, _table(_live_paths()))],
        ),
        patch("check_openapi_drift.fetch_spec_text", _fetch_pair),
        patch("check_openapi_drift.fetch_url_status", status),
    ):
        assert main() == 2
    out = capsys.readouterr().out
    assert "Site host outage" in out
    assert "Dead site route" not in out
    assert "New uncovered" not in out


def test_excluded_site_line_is_not_fetched(tmp_path: Path) -> None:
    calls: list[str] = []

    async def status(url: str) -> str:
        calls.append(url)
        return "200"

    with (
        patch(
            "sys.argv",
            ["check_openapi_drift.py", *_write_routes(tmp_path, _table(_live_paths()))],
        ),
        patch("check_openapi_drift.fetch_spec_text", _fetch_pair),
        patch("check_openapi_drift.fetch_url_status", status),
    ):
        assert main() == 0
    assert calls == [
        "https://site.web.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard?limit=1"
    ]


def test_fetch_spec_text_maps_timeout_and_http_status() -> None:
    async def slow(_url: str) -> httpx.Response:
        raise httpx.TimeoutException("slow")

    async def down(_url: str) -> httpx.Response:
        return httpx.Response(503, text="no")

    async def ok(_url: str) -> httpx.Response:
        return httpx.Response(200, text="<ok/>")

    with patch("check_openapi_drift._spec_get", slow):
        with pytest.raises(SpecFetchError, match="timeout"):
            asyncio.run(fetch_spec_text("https://sports.core.api.espn.com/v2/application.wadl"))
        assert asyncio.run(fetch_url_status("https://sports.core.api.espn.com/v2/x")) == "timeout"
    with patch("check_openapi_drift._spec_get", down):
        with pytest.raises(SpecFetchError, match="HTTP 503"):
            asyncio.run(fetch_spec_text("https://sports.core.api.espn.com/v3/application.wadl"))
        assert asyncio.run(fetch_url_status("https://sports.core.api.espn.com/v3/x")) == "503"
    with patch("check_openapi_drift._spec_get", ok):
        assert asyncio.run(
            fetch_spec_text("https://sports.core.api.espn.com/v2/application.wadl")
        ) == ("<ok/>")


def test_fetch_url_status_maps_connection_errors() -> None:
    async def broken(_url: str) -> httpx.Response:
        raise httpx.ConnectError("down")

    with patch("check_openapi_drift._spec_get", broken):
        assert (
            asyncio.run(fetch_url_status("https://sports.core.api.espn.com/v2/x")) == "connection"
        )


def test_committed_snapshot_parses() -> None:
    root = Path(__file__).parent.parent / "scripts"
    rows = load_route_table(root / "espn_routes_snapshot.tsv")
    v2 = [row for row in rows if row.path.startswith("/v2/")]
    v3 = [row for row in rows if row.path.startswith("/v3/")]
    assert len(v2) == 414
    assert len(v3) == 99
    assert {row.tag for row in rows} <= {"covered", "gap", "excluded", "deprecated"}
    assert sum(row.tag == "excluded" for row in rows) == 111
    assert sum(row.tag == "covered" and row.path.startswith("/v2/") for row in rows) == 25
    assert sum(row.tag == "covered" and row.path.startswith("/v3/") for row in rows) == 9
    assert sum(row.tag == "deprecated" for row in rows) == 0
    names = asyncio.run(_registered_tools())
    for row in rows:
        if row.tag == "covered":
            assert row.reason.split()[0] in names
    site = load_site_routes(root / "espn_site_routes.txt")
    assert sum(row.tag == "covered" for row in site) == 24
    assert sum(row.tag == "excluded" for row in site) == 1


def test_site_route_without_a_sample_url_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    site = "GET /apis/x  # covered: games_get_scoreboard\n"
    with patch(
        "sys.argv",
        ["check_openapi_drift.py", *_write_routes(tmp_path, _table(_live_paths()), site)],
    ):
        assert main() == 2
    assert "no sample URL" in capsys.readouterr().err
