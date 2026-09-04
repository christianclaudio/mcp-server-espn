"""Unit tests for the enterprise OpenAPI and parameter drift detection engine."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

scripts_dir = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(scripts_dir))

from check_openapi_drift import (  # noqa: E402
    DEFAULT_TEMPLATE_SPEC,
    is_parameter_deprecated,
    main,
    normalize_path,
    parse_spec,
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


def test_main_cli_spec_url() -> None:
    mock_resp = MagicMock()
    mock_resp.json.return_value = DEFAULT_TEMPLATE_SPEC
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.get", return_value=mock_resp):
        url = "https://api.example.com/openapi.json"
        with patch("sys.argv", ["check_openapi_drift.py", "--spec-url", url]):
            assert main() == 0
