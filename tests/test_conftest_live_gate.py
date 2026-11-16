"""The live marker runs the public ESPN sweep; it skips only for a missing name."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import (
    LIVE_REQUIRED_ENV,
    live_skip_reason,
    pytest_collection_modifyitems,
)

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"


class _Item:
    """Minimal item: only the marker lookup and add_marker the hook uses."""

    def __init__(self, live: bool) -> None:
        self._live = live
        self.markers: list[Any] = []

    def get_closest_marker(self, name: str) -> object | None:
        if self._live and name == "live":
            return object()
        return None

    def add_marker(self, marker: Any) -> None:
        self.markers.append(marker)


def _reason(marker: Any) -> str:
    reason = marker.kwargs["reason"]
    assert isinstance(reason, str)
    return reason


def test_public_api_needs_no_credential() -> None:
    """No credential is required, so the gate is open and ``-m live`` is unskipped."""
    assert LIVE_REQUIRED_ENV == ()
    assert live_skip_reason({}) is None


def test_missing_name_is_listed_without_its_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing required name is listed, and a value that is set is not."""
    monkeypatch.setattr(
        "tests.conftest.LIVE_REQUIRED_ENV",
        ("ESPN_BASE_URL", "ESPN_CORE_BASE_URL"),
    )
    reason = live_skip_reason({"ESPN_CORE_BASE_URL": "super-secret"})
    assert reason == "missing or empty: ESPN_BASE_URL"
    assert "super-secret" not in reason


def test_blank_value_counts_as_missing_and_is_not_echoed(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty or whitespace-only value counts as missing and is not echoed."""
    monkeypatch.setattr("tests.conftest.LIVE_REQUIRED_ENV", ("ESPN_BASE_URL",))
    reason = live_skip_reason({"ESPN_BASE_URL": "   "})
    assert reason == "missing or empty: ESPN_BASE_URL"
    assert "   " not in reason


def test_present_names_do_not_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every required name set to a non-blank value lets the item run."""
    monkeypatch.setattr("tests.conftest.LIVE_REQUIRED_ENV", ("ESPN_BASE_URL",))
    assert live_skip_reason({"ESPN_BASE_URL": "https://site.api.espn.com"}) is None


def test_default_addopts_deselect_live() -> None:
    """The default run deselects live, and the e2e marker is gone."""
    ini = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["pytest"]["ini_options"]
    assert "-m 'not live'" in ini["addopts"]
    assert "not e2e" not in ini["addopts"]
    markers = list(ini["markers"])
    assert markers == ["live: live network tests against the public ESPN API (run with -m live)"]


def test_hook_skips_live_items_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """The collection hook skips live items and leaves every other item alone."""
    monkeypatch.setattr(
        "tests.conftest.live_skip_reason",
        lambda environ=None: "missing or empty: ESPN_BASE_URL",
    )
    monkeypatch.setenv("ESPN_BASE_URL", "super-secret")
    live = _Item(True)
    other = _Item(False)
    pytest_collection_modifyitems(None, [live, other])  # type: ignore[arg-type]
    assert other.markers == []
    assert len(live.markers) == 1
    reason = _reason(live.markers[0])
    assert reason == "missing or empty: ESPN_BASE_URL"
    assert "super-secret" not in reason


def test_hook_leaves_live_items_when_the_gate_is_open() -> None:
    """With no required names, the hook adds no skip."""
    live = _Item(True)
    pytest_collection_modifyitems(None, [live])  # type: ignore[arg-type]
    assert live.markers == []
