"""fastmcp.json's bare fastmcp floor must match pyproject.toml's."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import pytest
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
FASTMCP_JSON = ROOT / "fastmcp.json"


def _bare_fastmcp_specifier(specs: list[str]) -> str:
    """Return the single ``>=`` lower-bound version for a bare ``fastmcp``.

    Other clauses (upper bounds, etc.) are ignored. A requirement that carries
    an environment marker is rejected.
    """
    matches: list[Requirement] = []
    for raw in specs:
        req = Requirement(raw)
        if req.name == "fastmcp" and not req.extras:
            marker_err = f"fastmcp must not carry an environment marker: {raw}"
            assert req.marker is None, marker_err
            matches.append(req)
    assert len(matches) == 1, specs
    lower_bounds: list[str] = []
    for spec in matches[0].specifier:
        if spec.operator == ">=":
            lower_bounds.append(spec.version)
    assert len(lower_bounds) == 1, specs
    return lower_bounds[0]


def test_fastmcp_json_floor_matches_pyproject() -> None:
    """A deploy from fastmcp.json must require the same fastmcp floor as the package."""
    project: dict[str, Any] = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    pyproject_specs: list[str] = list(project["dependencies"])
    manifest: dict[str, Any] = json.loads(FASTMCP_JSON.read_text(encoding="utf-8"))
    json_specs: list[str] = list(manifest["environment"]["dependencies"])
    assert _bare_fastmcp_specifier(json_specs) == _bare_fastmcp_specifier(pyproject_specs)


def test_upper_bound_in_one_list_still_matches_floor() -> None:
    """An upper bound in only one list must not fail when floors match."""
    left = _bare_fastmcp_specifier(["fastmcp>=4.1.5,<5"])
    right = _bare_fastmcp_specifier(["fastmcp>=4.1.5"])
    assert left == right


def test_environment_marker_is_rejected() -> None:
    """A bare fastmcp requirement must not carry an environment marker."""
    marked = 'fastmcp>=4.1.5; python_version < "3.12"'
    with pytest.raises(AssertionError, match="environment marker"):
        _bare_fastmcp_specifier([marked])
