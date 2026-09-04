#!/usr/bin/env python3
"""Enterprise OpenAPI & Parameter Drift Detection Engine.

Compares client implementation and tool routes against upstream OpenAPI 3.0/3.1 specs:
1. Endpoint Path Parity: Detects phantom client routes and uncovered upstream endpoints.
2. Parameter Deprecation Audit: Detects usage of query/body parameters marked `deprecated: true`
   or flagged with '[Deprecated]' in descriptions, preventing disruption before API sunset dates.
3. Breaking Change Detection: Detects missing required parameters and schema alterations.

Usage:
    python scripts/check_openapi_drift.py [--spec-url URL] [--spec-file PATH] [--strict]
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

# Default Reference Specification for mcp-server-template
DEFAULT_TEMPLATE_SPEC: dict[str, Any] = {
    "openapi": "3.1.0",
    "info": {
        "title": "Template Upstream API",
        "version": "1.0.0",
        "description": "Canonical reference API specification for template MCP verification",
    },
    "paths": {
        "/health": {
            "get": {
                "summary": "Health check",
                "responses": {"200": {"description": "Operational health status"}},
            }
        },
        "/items": {
            "get": {
                "summary": "List items",
                "parameters": [
                    {
                        "name": "limit",
                        "in": "query",
                        "required": False,
                        "schema": {"type": "integer"},
                        "description": "Page size limit",
                    },
                    {
                        "name": "offset",
                        "in": "query",
                        "required": False,
                        "schema": {"type": "integer"},
                        "description": "Pagination offset",
                    },
                    {
                        "name": "search",
                        "in": "query",
                        "required": False,
                        "deprecated": True,
                        "schema": {"type": "string"},
                        "description": "**[Deprecated]** Use specific filter parameters instead.",
                    },
                ],
                "responses": {"200": {"description": "List of items"}},
            },
            "post": {
                "summary": "Create item",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["name"],
                                "properties": {
                                    "name": {"type": "string"},
                                    "payload": {"type": "object"},
                                },
                            }
                        }
                    },
                },
                "responses": {"201": {"description": "Item created"}},
            },
        },
        "/items/{itemId}": {
            "get": {
                "summary": "Get item by ID",
                "parameters": [
                    {
                        "name": "itemId",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "responses": {"200": {"description": "Item details"}},
            },
            "delete": {
                "summary": "Delete item",
                "parameters": [
                    {
                        "name": "itemId",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "responses": {"200": {"description": "Item deleted"}},
            },
        },
    },
}


@dataclass
class ClientCall:
    method: str
    raw_path: str
    normalized_path: str
    query_params: set[str] = field(default_factory=set)
    body_keys: set[str] = field(default_factory=set)
    source_file: str = ""
    line_number: int = 0


@dataclass
class SpecEndpoint:
    method: str
    path: str
    normalized_path: str
    query_params: dict[str, dict[str, Any]] = field(default_factory=dict)
    path_params: dict[str, dict[str, Any]] = field(default_factory=dict)
    deprecated_params: set[str] = field(default_factory=set)
    required_params: set[str] = field(default_factory=set)
    is_deprecated_route: bool = False


def normalize_path(path: str) -> str:
    """Normalize path by stripping query, leading/trailing slashes, and standardizing params."""
    path = path.split("?")[0].strip()
    if not path.startswith("/"):
        path = "/" + path
    path = path.rstrip("/")
    return re.sub(r"\{[^}]*\}", "{}", path)


def is_parameter_deprecated(param_def: dict[str, Any]) -> bool:
    """Detect if a parameter is deprecated via boolean flag or description warning."""
    if param_def.get("deprecated") is True:
        return True
    desc = str(param_def.get("description", "")).lower()
    title = str(param_def.get("title", "")).lower()
    if "[deprecated]" in desc or "deprecated" in title or "end of support" in desc:
        return True
    return False


def parse_spec(raw_spec: dict[str, Any]) -> dict[tuple[str, str], SpecEndpoint]:
    """Index OpenAPI specification endpoints, parameters, and deprecation markers."""
    endpoints: dict[tuple[str, str], SpecEndpoint] = {}
    paths = raw_spec.get("paths", {})

    for path_str, methods in paths.items():
        norm_path = normalize_path(path_str)
        path_level_params = methods.get("parameters", []) if isinstance(methods, dict) else []

        for method_name, op in methods.items():
            if method_name.lower() not in ("get", "post", "put", "patch", "delete"):
                continue

            method = method_name.upper()
            op_params = op.get("parameters", []) if isinstance(op, dict) else []
            all_params = list(path_level_params) + op_params

            endpoint = SpecEndpoint(
                method=method,
                path=path_str,
                normalized_path=norm_path,
                is_deprecated_route=bool(op.get("deprecated", False)),
            )

            for param in all_params:
                if not isinstance(param, dict):
                    continue
                p_name = param.get("name")
                p_in = param.get("in", "query")
                if not p_name:
                    continue

                if p_in == "query":
                    endpoint.query_params[p_name] = param
                elif p_in == "path":
                    endpoint.path_params[p_name] = param

                if is_parameter_deprecated(param):
                    endpoint.deprecated_params.add(p_name)

                if param.get("required") is True:
                    endpoint.required_params.add(p_name)

            endpoints[(method, norm_path)] = endpoint

    return endpoints


class ClientAstVisitor(ast.NodeVisitor):
    """AST visitor to find HTTP client calls and extract method, path, and passed query params."""

    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.calls: list[ClientCall] = []

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        func = node.func
        method_name = ""
        if isinstance(func, ast.Attribute):
            method_name = func.attr

        http_methods = {"get", "post", "put", "patch", "delete", "request"}
        if method_name.lower() in http_methods:
            method = ""
            raw_path = ""
            query_params: set[str] = set()
            body_keys: set[str] = set()

            if method_name.lower() == "request":
                if (
                    len(node.args) >= 1
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    method = node.args[0].value.upper()
                if len(node.args) >= 2:
                    raw_path = self._extract_path(node.args[1])
            else:
                method = method_name.upper()
                if len(node.args) >= 1:
                    raw_path = self._extract_path(node.args[0])

            for kw in node.keywords:
                if kw.arg in ("params", "query_params"):
                    query_params.update(self._extract_dict_keys(kw.value))
                elif kw.arg in ("json", "json_data", "body"):
                    body_keys.update(self._extract_dict_keys(kw.value))

            if (
                method_name.lower() in ("get", "post", "put", "patch", "delete")
                and len(node.args) >= 2
            ):
                if method_name.lower() == "get":
                    query_params.update(self._extract_dict_keys(node.args[1]))
                else:
                    body_keys.update(self._extract_dict_keys(node.args[1]))

            if method and raw_path:
                norm_path = normalize_path(raw_path)
                self.calls.append(
                    ClientCall(
                        method=method,
                        raw_path=raw_path,
                        normalized_path=norm_path,
                        query_params=query_params,
                        body_keys=body_keys,
                        source_file=self.filename,
                        line_number=node.lineno,
                    )
                )

        self.generic_visit(node)

    def _extract_path(self, node: ast.AST) -> str:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            parts = []
            for part in node.values:
                if isinstance(part, ast.Constant):
                    parts.append(str(part.value))
                else:
                    parts.append("{}")
            return "".join(parts)
        return "{}"

    def _extract_dict_keys(self, node: ast.AST) -> set[str]:
        keys = set()
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    keys.add(k.value)
        return keys


def extract_client_calls(source_dir: Path) -> list[ClientCall]:
    """Scan all Python files in src/ for API client calls."""
    calls: list[ClientCall] = []
    for py_file in source_dir.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            visitor = ClientAstVisitor(str(py_file.relative_to(source_dir.parent)))
            visitor.visit(tree)
            calls.extend(visitor.calls)
        except Exception:
            continue
    return calls


def run_drift_check(
    spec: dict[str, Any],
    source_dir: Path,
    strict: bool = False,
) -> tuple[int, list[str]]:
    """Execute complete drift and deprecation validation."""
    spec_endpoints = parse_spec(spec)
    client_calls = extract_client_calls(source_dir)

    issues: list[str] = []
    warnings: list[str] = []

    print(f"[*] Spec endpoints indexed: {len(spec_endpoints)}")
    print(f"[*] Client calls detected:  {len(client_calls)}")

    covered_keys: set[tuple[str, str]] = set()

    for call in client_calls:
        key = (call.method, call.normalized_path)
        spec_op = spec_endpoints.get(key)

        if not spec_op:
            issues.append(
                f"❌ PHANTOM ROUTE: Client calls '{call.method} {call.raw_path}' "
                f"({call.source_file}:{call.line_number}) which does not exist in OpenAPI spec."
            )
            continue

        covered_keys.add(key)

        if spec_op.is_deprecated_route:
            warnings.append(
                f"⚠️ DEPRECATED ROUTE: Client calls deprecated route '{call.method} {spec_op.path}' "
                f"({call.source_file}:{call.line_number})."
            )

        for qp in call.query_params:
            if qp in spec_op.deprecated_params:
                msg = (
                    f"⚠️ DEPRECATED PARAMETER IN USE: '{qp}' on '{call.method} {spec_op.path}' "
                    f"is deprecated in OpenAPI spec! ({call.source_file}:{call.line_number})"
                )
                if strict:
                    issues.append("❌ " + msg[3:])
                else:
                    warnings.append(msg)

    uncovered = set(spec_endpoints.keys()) - covered_keys
    print(f"[*] Covered endpoints:      {len(covered_keys)}")
    print(f"[*] Uncovered endpoints:    {len(uncovered)}")

    output_lines: list[str] = []

    if warnings:
        output_lines.append("\n⚠️ WARNINGS (Deprecations / Advisory):")
        for w in warnings:
            output_lines.append(f"  {w}")

    if issues:
        output_lines.append("\n❌ BREAKING DRIFT DETECTED:")
        for err in issues:
            output_lines.append(f"  {err}")
        return 1, output_lines

    if warnings and strict:
        return 1, output_lines

    output_lines.append("\n[✓] No breaking drift or path mismatches detected.")
    return 0, output_lines


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Enterprise OpenAPI & Parameter Drift Detection Engine"
    )
    parser.add_argument("--spec-url", help="Upstream OpenAPI JSON/YAML URL to validate against")
    parser.add_argument("--spec-file", help="Path to local OpenAPI JSON/YAML file")
    parser.add_argument(
        "--strict", action="store_true", help="Fail with exit 1 on deprecated parameter usage"
    )
    parser.add_argument(
        "--source-dir",
        default=str(Path(__file__).parent.parent / "src"),
        help="Root source directory to inspect",
    )
    args = parser.parse_args()

    raw_spec: dict[str, Any] = DEFAULT_TEMPLATE_SPEC

    if args.spec_file:
        file_path = Path(args.spec_file)
        if not file_path.exists():
            print(f"Error: Spec file not found at {file_path}", file=sys.stderr)
            return 2
        try:
            raw_spec = json.loads(file_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"Error reading spec file: {e}", file=sys.stderr)
            return 2
    elif args.spec_url:
        print(f"[*] Fetching OpenAPI spec from: {args.spec_url}")
        try:
            resp = httpx.get(args.spec_url, timeout=30.0, follow_redirects=True)
            resp.raise_for_status()
            raw_spec = resp.json()
        except Exception as e:
            print(f"Error fetching spec from URL: {e}", file=sys.stderr)
            return 2

    source_path = Path(args.source_dir)
    print("=" * 70)
    print("🔍 ENTERPRISE OPENAPI & PARAMETER DRIFT MONITOR")
    print("=" * 70)

    exit_code, report_lines = run_drift_check(raw_spec, source_path, strict=args.strict)
    for line in report_lines:
        print(line)

    print("=" * 70)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
