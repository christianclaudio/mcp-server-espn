"""HTTP bearer auth and the non-localhost bind policy (template v1.6.0)."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

import espn_mcp.server as srv
from espn_mcp.auth import (
    ALLOW_UNAUTHENTICATED_BIND_ENV,
    AUTH_TOKEN_ENV,
    SharedTokenVerifier,
    allow_unauthenticated_bind,
    is_localhost,
    read_auth_token,
)

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "correct-horse-battery-staple-69"
HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "MCP-Protocol-Version": "2026-07-28",
    "Mcp-Method": "server/discover",
}
DISCOVER = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "server/discover",
    "params": {
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": {},
        }
    },
}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(AUTH_TOKEN_ENV, raising=False)
    monkeypatch.delenv(ALLOW_UNAUTHENTICATED_BIND_ENV, raising=False)


@pytest.fixture
def run_args(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """``main()`` builds its own server; capture it as ``srv.mcp`` with ``run`` stubbed out."""
    captured: dict[str, Any] = {}
    real_create = srv.create_server

    def _create(*args: Any, **kwargs: Any) -> Any:
        fresh = real_create(*args, **kwargs)
        monkeypatch.setattr(fresh, "run", lambda **kw: captured.update(kw))
        monkeypatch.setattr(srv, "mcp", fresh)
        return fresh

    monkeypatch.setattr(srv, "create_server", _create)
    return captured


def _main(monkeypatch: pytest.MonkeyPatch, *argv: str) -> None:
    monkeypatch.setattr("sys.argv", ["espn-mcp", *argv])
    srv.main()


PUBLIC_HTTP = (
    "--transport",
    "streamable-http",
    "--host",
    "0.0.0.0",
    "--allowed-host",
    "mcp.internal",
)


@pytest.mark.parametrize("presented", ["", "   ", "\t\n"])
async def test_verify_token_blank_check_is_load_bearing(presented: str) -> None:
    """verify_token rejects a blank token itself, not only via the compare.

    Forcing the expected token to match the blank value proves the early
    ``if not token.strip()`` return is what refuses it.
    """
    verifier = SharedTokenVerifier(TOKEN)
    verifier._expected = presented.encode("utf-8")
    assert await verifier.verify_token(presented) is None


# ── Fix 1: whitespace-only token is unset; verifier refuses blank ──────────────


@pytest.mark.parametrize("value", ["", "   ", "\t\n"])
def test_whitespace_token_is_unset(
    monkeypatch: pytest.MonkeyPatch,
    run_args: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
    value: str,
) -> None:
    monkeypatch.setenv(AUTH_TOKEN_ENV, value)
    assert read_auth_token() == ""
    with caplog.at_level(logging.WARNING, logger="espn_mcp.server"):
        _main(monkeypatch, "--transport", "streamable-http")
    assert run_args["transport"] == "streamable-http"
    assert srv.mcp.auth is None
    assert f"{AUTH_TOKEN_ENV} is not set" in caplog.text


def test_token_is_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(AUTH_TOKEN_ENV, f"  {TOKEN}\n")
    assert read_auth_token() == TOKEN


@pytest.mark.parametrize("value", ["", "   ", "\n\t"])
def test_verifier_refuses_blank_expected_token(value: str) -> None:
    with pytest.raises(ValueError, match=AUTH_TOKEN_ENV):
        SharedTokenVerifier(value)


@pytest.mark.asyncio
async def test_verifier_contract() -> None:
    verifier = SharedTokenVerifier(f" {TOKEN} ")
    assert TOKEN not in repr(verifier)
    assert "REDACTED" in repr(verifier)
    assert await verifier.verify_token("") is None
    assert await verifier.verify_token("   ") is None
    assert await verifier.verify_token("wrong") is None
    ok = await verifier.verify_token(TOKEN)
    assert ok is not None and ok.client_id == "espn-mcp-shared-token"


# ── Fix 2: bind policy ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "[::1]", "localhost", "LOCALHOST"])
def test_is_localhost_true(host: str) -> None:
    assert is_localhost(host)


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "10.0.0.5", "mcp.internal", "127.0.0.2"])
def test_is_localhost_false(host: str) -> None:
    assert not is_localhost(host)


@pytest.mark.parametrize("value", ["1", "true", "YES", " on "])
def test_allow_unauthenticated_bind_truthy(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(ALLOW_UNAUTHENTICATED_BIND_ENV, value)
    assert allow_unauthenticated_bind()


@pytest.mark.parametrize("value", ["", "0", "false", "no", "2"])
def test_allow_unauthenticated_bind_falsy(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(ALLOW_UNAUTHENTICATED_BIND_ENV, value)
    assert not allow_unauthenticated_bind()


@pytest.mark.parametrize(
    "argv",
    [
        PUBLIC_HTTP,
        ("--transport", "sse", "--host", "0.0.0.0", "--allowed-host", "mcp.internal"),
        ("--transport", "streamable-http", "--host", "10.0.0.5"),
        ("--transport", "sse", "--host", "::"),
    ],
)
def test_non_localhost_without_token_refuses(
    monkeypatch: pytest.MonkeyPatch,
    run_args: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
    argv: tuple[str, ...],
) -> None:
    monkeypatch.setenv(AUTH_TOKEN_ENV, "   ")
    with pytest.raises(SystemExit) as exc:
        _main(monkeypatch, *argv)
    assert exc.value.code == 2
    assert run_args == {}
    err = capsys.readouterr().err
    assert "refusing to serve" in err
    assert AUTH_TOKEN_ENV in err and ALLOW_UNAUTHENTICATED_BIND_ENV in err


def test_refusal_message_has_no_secret(
    monkeypatch: pytest.MonkeyPatch, run_args: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(ALLOW_UNAUTHENTICATED_BIND_ENV, "0")
    monkeypatch.setenv("ESPN_API_KEY", "upstream-secret-69")
    with pytest.raises(SystemExit):
        _main(monkeypatch, *PUBLIC_HTTP)
    assert "upstream-secret-69" not in capsys.readouterr().err


@pytest.mark.parametrize("transport", ["streamable-http", "sse"])
def test_non_localhost_with_token_starts_with_auth(
    monkeypatch: pytest.MonkeyPatch,
    run_args: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
    transport: str,
) -> None:
    monkeypatch.setenv(AUTH_TOKEN_ENV, TOKEN)
    with caplog.at_level(logging.INFO, logger="espn_mcp.server"):
        _main(
            monkeypatch,
            "--transport",
            transport,
            "--host",
            "0.0.0.0",
            "--allowed-host",
            "mcp.internal",
        )
    assert run_args["transport"] == transport
    assert run_args["host"] == "0.0.0.0"
    assert isinstance(srv.mcp.auth, SharedTokenVerifier)
    assert "authentication is on" in caplog.text
    assert TOKEN not in caplog.text


def test_non_localhost_with_opt_in_starts_with_warning(
    monkeypatch: pytest.MonkeyPatch, run_args: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(ALLOW_UNAUTHENTICATED_BIND_ENV, "1")
    with caplog.at_level(logging.WARNING, logger="espn_mcp.server"):
        _main(monkeypatch, *PUBLIC_HTTP)
    assert run_args["transport"] == "streamable-http"
    assert srv.mcp.auth is None
    assert f"{ALLOW_UNAUTHENTICATED_BIND_ENV} is set" in caplog.text
    assert f"{AUTH_TOKEN_ENV} is not set" in caplog.text


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
@pytest.mark.parametrize("transport", ["streamable-http", "sse"])
def test_localhost_without_token_allowed(
    monkeypatch: pytest.MonkeyPatch, run_args: dict[str, Any], transport: str, host: str
) -> None:
    _main(monkeypatch, "--transport", transport, "--host", host)
    assert run_args["transport"] == transport
    assert srv.mcp.auth is None


def test_stdio_ignores_bind_policy_and_token(
    monkeypatch: pytest.MonkeyPatch, run_args: dict[str, Any]
) -> None:
    monkeypatch.setenv(AUTH_TOKEN_ENV, TOKEN)
    _main(monkeypatch, "--transport", "stdio", "--host", "0.0.0.0")
    assert run_args == {"transport": "stdio"}
    # espn's main() rebuilds the server, so the build-time verifier is present; FastMCP
    # applies server auth only to HTTP transports, so stdio is unaffected.
    assert isinstance(srv.mcp.auth, SharedTokenVerifier)


# ── Fix 3: the image's default command is stdio and passes the bind guard ──────


def _dockerfile_default_argv() -> list[str]:
    """Exec-form ENTRYPOINT + CMD of the Dockerfile's last stage, as the container runs it."""
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    last_stage = re.split(r"^FROM\s", text, flags=re.MULTILINE)[-1]
    found: dict[str, list[str]] = {}
    for kind in ("ENTRYPOINT", "CMD"):
        lines = re.findall(rf"^{kind}\s+(\[.*\])\s*$", last_stage, flags=re.MULTILINE)
        if lines:
            found[kind] = json.loads(lines[-1])
    argv = found.get("ENTRYPOINT", []) + found.get("CMD", [])
    assert argv, "Dockerfile runtime stage has no exec-form ENTRYPOINT/CMD"
    return argv


def test_image_default_command_is_stdio(
    monkeypatch: pytest.MonkeyPatch, run_args: dict[str, Any]
) -> None:
    argv = _dockerfile_default_argv()
    assert argv[0] == "espn-mcp"
    monkeypatch.setattr("sys.argv", argv)
    srv.main()
    assert run_args == {"transport": "stdio"}
    assert srv.mcp.auth is None


def test_readme_image_http_command_needs_and_attaches_token(
    monkeypatch: pytest.MonkeyPatch, run_args: dict[str, Any]
) -> None:
    """The README's `docker run` over HTTP passes the token and binds 0.0.0.0."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"-e {AUTH_TOKEN_ENV} " in readme
    assert "change-me" not in readme
    assert "--allowed-host localhost" not in readme
    assert '--allowed-host "$PUBLIC_HOST"' in readme
    assert "<public-host>" not in readme
    assert "--host 0.0.0.0" in readme
    monkeypatch.setenv(AUTH_TOKEN_ENV, TOKEN)
    _main(monkeypatch, *PUBLIC_HTTP)
    assert isinstance(srv.mcp.auth, SharedTokenVerifier)


# ── HTTP 401 / 401 / 200 ───────────────────────────────────────────────────────


async def _client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
        ) as client:
            yield client


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("auth_header", "expected"),
    [(None, 401), ("Bearer wrong-token", 401), ("Bearer    ", 401), (f"Bearer {TOKEN}", 200)],
)
async def test_streamable_http_enforces_token(auth_header: str | None, expected: int) -> None:
    server = srv.create_server()
    server.auth = SharedTokenVerifier(TOKEN)
    app = server.streamable_http_app(stateless_http=True, json_response=True)
    headers = dict(HEADERS)
    if auth_header is not None:
        headers["Authorization"] = auth_header
    async for client in _client(app):
        res = await client.post("/mcp", json=DISCOVER, headers=headers)
        assert res.status_code == expected
        assert TOKEN not in res.text
        if expected == 200:
            assert "supportedVersions" in res.json()["result"]
        else:
            assert res.headers["www-authenticate"].lower().startswith("bearer")


# ── the token is enforced on every HTTP entry point ────────────────

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "t", "version": "1"},
    },
}


def test_create_server_attaches_token_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(AUTH_TOKEN_ENV, f"  {TOKEN}\n")
    assert isinstance(srv.create_server().auth, SharedTokenVerifier)


@pytest.mark.parametrize("value", ["", "   "])
def test_create_server_blank_token_means_no_auth(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(AUTH_TOKEN_ENV, value)
    assert srv.create_server().auth is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("auth_header", "expected"),
    [(None, 401), ("Bearer wrong-token", 401), (f"Bearer {TOKEN}", 200)],
)
async def test_plain_http_app_enforces_env_token(
    monkeypatch: pytest.MonkeyPatch, auth_header: str | None, expected: int
) -> None:
    """``mcp.http_app()`` (what ASGI hosts mount) enforces the env token with no ``main()``."""
    monkeypatch.setenv(AUTH_TOKEN_ENV, TOKEN)
    app = srv.create_server().http_app(stateless_http=True, json_response=True)
    headers = dict(HEADERS)
    if auth_header is not None:
        headers["Authorization"] = auth_header
    async for client in _client(app):
        res = await client.post("/mcp", json=DISCOVER, headers=headers)
        assert res.status_code == expected
        assert TOKEN not in res.text


def test_fastmcp_run_entry_point_enforces_env_token() -> None:
    """``fastmcp run server.py:mcp --transport http``: a fresh import with the token set.

    ``fastmcp run`` imports the module and serves its ``mcp`` object without ``main()``,
    so the module-level server must already carry the verifier. A subprocess import is
    the same path, then ``initialize`` over the served ASGI app is refused without a token.
    """
    import subprocess
    import sys

    code = (
        "import asyncio, httpx, json, sys\n"
        "import espn_mcp.server as s\n"
        "app = s.mcp.http_app(transport='http', stateless_http=True, json_response=True)\n"
        "async def go():\n"
        "    async with app.router.lifespan_context(app):\n"
        "        t = httpx.ASGITransport(app=app)\n"
        "        async with httpx.AsyncClient(transport=t, base_url='http://127.0.0.1') as c:\n"
        "            h = {'Accept': 'application/json, text/event-stream',\n"
        "                 'Content-Type': 'application/json'}\n"
        "            body = json.loads(sys.argv[1])\n"
        "            a = await c.post('/mcp', json=body, headers=h)\n"
        "            h['Authorization'] = 'Bearer ' + sys.argv[2]\n"
        "            b = await c.post('/mcp', json=body, headers=h)\n"
        "            print(int(a.status_code), int(b.status_code))\n"
        "asyncio.run(go())\n"
    )
    env = {k: v for k, v in __import__("os").environ.items() if not k.startswith("ESPN_")}
    env[AUTH_TOKEN_ENV] = TOKEN
    out = subprocess.run(
        [sys.executable, "-c", code, json.dumps(INITIALIZE), TOKEN],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    assert out.stdout.split() == ["401", "200"]


def test_serve_time_attach_when_token_set_after_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """A token set after the server was built is attached when ``main()`` serves."""
    import argparse

    server = srv.create_server()
    assert server.auth is None
    monkeypatch.setenv(AUTH_TOKEN_ENV, TOKEN)
    srv._apply_http_auth(argparse.ArgumentParser(), server, "streamable-http", "0.0.0.0")
    assert isinstance(server.auth, SharedTokenVerifier)


def test_wildcard_with_token_still_needs_allowed_host(
    monkeypatch: pytest.MonkeyPatch, run_args: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(AUTH_TOKEN_ENV, TOKEN)
    with pytest.raises(SystemExit) as exc:
        _main(monkeypatch, "--transport", "streamable-http", "--host", "0.0.0.0")
    assert exc.value.code == 2
    assert run_args == {}
    assert "--allowed-host is required" in capsys.readouterr().err
