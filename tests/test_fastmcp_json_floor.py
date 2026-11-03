"""fastmcp.json's bare fastmcp floor must match pyproject.toml's."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
FASTMCP_JSON = ROOT / "fastmcp.json"


def _bare_fastmcp_specifier(specs: list[str]) -> str:
    """Return the single ``>=`` specifier for a bare ``fastmcp`` requirement."""
    matches: list[str] = []
    for raw in specs:
        req = Requirement(raw)
        if req.name == "fastmcp" and not req.extras:
            matches.append(str(req.specifier))
    assert len(matches) == 1, specs
    return matches[0]


def test_fastmcp_json_floor_matches_pyproject() -> None:
    """A deploy from fastmcp.json must require the same fastmcp floor as the package."""
    project: dict[str, Any] = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    pyproject_specs: list[str] = list(project["dependencies"])
    manifest: dict[str, Any] = json.loads(FASTMCP_JSON.read_text(encoding="utf-8"))
    json_specs: list[str] = list(manifest["environment"]["dependencies"])
    assert _bare_fastmcp_specifier(json_specs) == _bare_fastmcp_specifier(pyproject_specs)
