"""The autouse mock skips ``live`` tests, which run the normal client and its SSRF guard.

An inner pytest session (in a subprocess, so this run's own mock is not in play) loads the
real conftest and runs two tests that make no request: one marked ``live``, one not.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_INNER_TESTS = """
import asyncio

import httpx
import pytest

from espn_mcp import client as client_module
from espn_mcp import server
from espn_mcp.client import SSRFSafeAsyncTransport


@pytest.mark.live
def test_live_gets_the_guarded_default_client():
    default = client_module.default_client
    assert server.client is default
    assert client_module.get_client() is default
    assert default._custom_client is None
    http = asyncio.run(default.get_client())
    assert isinstance(http._transport, SSRFSafeAsyncTransport)
    assert not isinstance(http._transport, httpx.MockTransport)


def test_default_run_gets_the_mock_transport():
    default = client_module.default_client
    assert isinstance(default._custom_client._transport, httpx.MockTransport)
    assert isinstance(server.client._custom_client._transport, httpx.MockTransport)
    assert client_module.get_client() is server.client
"""


def test_autouse_mock_skips_live_tests_only(pytester: pytest.Pytester) -> None:
    conftest = Path(__file__).with_name("conftest.py").read_text(encoding="utf-8")
    pytester.makeconftest(conftest)
    pytester.makeini("[pytest]\nmarkers =\n    live: live tests\n")
    pytester.makepyfile(test_inner=_INNER_TESTS)
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider", "-p", "no:cov", "-v")
    result.assert_outcomes(passed=2)
    result.stdout.fnmatch_lines(
        [
            "*test_live_gets_the_guarded_default_client PASSED*",
            "*test_default_run_gets_the_mock_transport PASSED*",
        ]
    )
