"""Every shared floor in fastmcp.json must match pyproject.toml.

FastMCP deprecations are errors under the configured pytest filter. The filter
is a warning category, not a module regex: FastMCP emits
``FastMCPDeprecationWarning`` with a stacklevel that points at the caller.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
FASTMCP_JSON = ROOT / "fastmcp.json"

# Names fastmcp.json and the package install together. Every name in the
# fastmcp.json environment list is checked; this set is the one the manifest
# is expected to carry.
SHARED_FLOOR_NAMES = frozenset(
    {"mcp", "fastmcp", "certifi", "httpx", "pydantic", "pydantic-settings"}
)


def _floors(specs: list[str]) -> dict[str, Version]:
    """Return ``{canonical name: the single >= Version}`` for bare requirements.

    A requirement that names extras is skipped. A requirement that carries an
    environment marker is rejected. Other clauses, such as an upper bound, are
    ignored. Each name may appear once.
    """
    floors: dict[str, Version] = {}
    for raw in specs:
        req = Requirement(raw)
        if req.extras:
            continue
        name = canonicalize_name(req.name)
        assert req.marker is None, f"{name} must not carry an environment marker: {raw}"
        lower_bounds = [Version(spec.version) for spec in req.specifier if spec.operator == ">="]
        assert len(lower_bounds) == 1, raw
        assert name not in floors, raw
        floors[name] = lower_bounds[0]
    return floors


def _assert_same_floors(upstream: dict[str, Version], project: dict[str, Version]) -> None:
    """Every upstream name is in ``project`` at the same floor."""
    missing = [name for name in upstream if name not in project]
    assert not missing, "missing from pyproject: " + ", ".join(missing)
    drifted = [name for name in upstream if project[name] != upstream[name]]
    assert not drifted, "floor drift: " + ", ".join(drifted)


def test_fastmcp_json_floors_match_pyproject() -> None:
    """A deploy from fastmcp.json must require the same floors as the package."""
    project: dict[str, Any] = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    manifest: dict[str, Any] = json.loads(FASTMCP_JSON.read_text(encoding="utf-8"))
    json_floors = _floors(list(manifest["environment"]["dependencies"]))
    project_floors = _floors(list(project["dependencies"]))
    assert set(json_floors) >= SHARED_FLOOR_NAMES
    _assert_same_floors(json_floors, project_floors)


def test_upper_bound_in_one_list_still_matches_floor() -> None:
    """An upper bound in only one list must not fail when floors match."""
    left = _floors(["fastmcp>=4.1.5,<5"])
    right = _floors(["fastmcp>=4.1.5"])
    assert left == right


def test_environment_marker_is_rejected() -> None:
    """A bare requirement must not carry an environment marker."""
    marked = 'fastmcp>=4.1.5; python_version < "3.12"'
    with pytest.raises(AssertionError, match="environment marker"):
        _floors([marked])


def test_fastmcp_name_is_matched_case_insensitively() -> None:
    """A capitalized FastMCP name must still be recognised as the floor."""
    assert _floors(["FastMCP>=4.1.5"]) == {"fastmcp": Version("4.1.5")}


def test_equivalent_version_strings_compare_equal() -> None:
    """Trailing .0 components must not fail the floor compare."""
    left = _floors(["fastmcp>=4.1.5.0"])
    right = _floors(["fastmcp>=4.1.5"])
    assert left == right


def test_extras_are_skipped() -> None:
    """An extra is not a shared floor, so it is left out of the compare."""
    assert _floors(["fastmcp[code-mode]>=9", "httpx>=0.28.1"]) == {"httpx": Version("0.28.1")}


def test_drifted_non_fastmcp_floor_fails() -> None:
    """A pydantic floor that differs between the two lists is a failure."""
    upstream = _floors(["pydantic>=2.14.2", "fastmcp>=4.2.0"])
    project = _floors(["pydantic>=2.14.3", "fastmcp>=4.2.0"])
    with pytest.raises(AssertionError, match="pydantic"):
        _assert_same_floors(upstream, project)


def test_name_missing_from_pyproject_fails() -> None:
    """A fastmcp.json dependency that pyproject does not pin is a failure."""
    upstream = _floors(["httpx>=0.28.1"])
    project = _floors(["fastmcp>=4.2.0"])
    with pytest.raises(AssertionError, match="httpx"):
        _assert_same_floors(upstream, project)


def test_fastmcp_deprecation_is_an_error_and_plain_deprecation_warns(
    pytester: pytest.Pytester,
) -> None:
    """Under the configured ini filter, FastMCPDeprecationWarning raises and
    a plain DeprecationWarning only warns.
    """
    ini = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["pytest"]["ini_options"]
    filters = list(ini["filterwarnings"])
    assert filters == ["error::fastmcp.exceptions.FastMCPDeprecationWarning"]
    pytester.makepyprojecttoml(
        "[tool.pytest.ini_options]\nfilterwarnings = " + json.dumps(filters) + "\n"
    )
    pytester.makepyfile(
        """
        import warnings

        import pytest
        from fastmcp.exceptions import FastMCPDeprecationWarning

        def test_fastmcp_deprecation_raises():
            with pytest.raises(FastMCPDeprecationWarning):
                warnings.warn(
                    "removed in the next release",
                    FastMCPDeprecationWarning,
                    stacklevel=2,
                )

        def test_plain_deprecation_warns():
            with pytest.warns(DeprecationWarning):
                warnings.warn("old api", DeprecationWarning, stacklevel=2)
        """
    )
    result = pytester.runpytest_subprocess("--noconftest", "-p", "no:cacheprovider", "-q")
    result.assert_outcomes(passed=2)
