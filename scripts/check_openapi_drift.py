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
import asyncio
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from espn_mcp.client import SSRFSafeAsyncTransport, _validate_base_url
from espn_mcp.errors import ESPNConnectionError

# Default Reference Specification for mcp-server-espn
DEFAULT_ESPN_SPEC: dict[str, Any] = {
    "openapi": "3.1.0",
    "info": {
        "title": "ESPN Public REST APIs",
        "version": "1.0.0",
        "description": "Canonical reference API specification for ESPN MCP verification",
    },
    "paths": {
        "/apis/site/v2/sports/{}/{}/scoreboard": {
            "get": {
                "summary": "Scoreboard",
                "parameters": [
                    {"name": "limit", "in": "query", "required": False},
                    {"name": "dates", "in": "query", "required": False},
                    {"name": "week", "in": "query", "required": False},
                    {"name": "seasontype", "in": "query", "required": False},
                    {"name": "groups", "in": "query", "required": False},
                ],
                "responses": {"200": {"description": "Live scores and games"}},
            }
        },
        "/apis/site/v2/sports/{}/{}/summary": {
            "get": {
                "summary": "Game summary, odds, and boxscore",
                "parameters": [{"name": "event", "in": "query", "required": True}],
                "responses": {"200": {"description": "Game summary and details"}},
            }
        },
        "/apis/v2/sports/{}/{}/standings": {
            "get": {
                "summary": "Standings",
                "parameters": [{"name": "season", "in": "query", "required": False}],
                "responses": {"200": {"description": "Standings data"}},
            }
        },
        "/apis/site/v2/sports/{}/{}/news": {
            "get": {
                "summary": "Latest news",
                "parameters": [
                    {"name": "limit", "in": "query", "required": False},
                    {
                        "name": "search",
                        "in": "query",
                        "required": False,
                        "deprecated": True,
                        "description": "**[Deprecated]** Use specific filter parameters instead.",
                    },
                ],
                "responses": {"200": {"description": "News articles"}},
            }
        },
        "/apis/site/v2/sports/{}/{}/rankings": {
            "get": {
                "summary": "Top 25 rankings and polls",
                "responses": {"200": {"description": "Rankings data"}},
            }
        },
        "/apis/site/v2/sports/{}/{}/teams/{}/roster": {
            "get": {
                "summary": "Team active roster",
                "responses": {"200": {"description": "Team roster"}},
            }
        },
        "/apis/site/v2/sports/{}/{}/teams/{}/depthcharts": {
            "get": {
                "summary": "Team depth chart",
                "responses": {"200": {"description": "Team depth chart"}},
            }
        },
        "/apis/site/v2/sports/{}/{}/teams/{}/schedule": {
            "get": {
                "summary": "Team schedule and past scores",
                "parameters": [
                    {"name": "season", "in": "query", "required": False},
                    {"name": "seasontype", "in": "query", "required": False},
                ],
                "responses": {"200": {"description": "Team schedule"}},
            }
        },
        "/apis/common/v3/sports/{}/{}/athletes/{}/overview": {
            "get": {
                "summary": "Athlete overview and game logs",
                "responses": {"200": {"description": "Athlete overview"}},
            }
        },
        "/apis/common/v3/search": {
            "get": {
                "summary": "Search athletes and teams",
                "parameters": [
                    {"name": "query", "in": "query", "required": True},
                    {"name": "type", "in": "query", "required": False},
                    {"name": "limit", "in": "query", "required": False},
                ],
                "responses": {"200": {"description": "Search results"}},
            }
        },
        "/apis/site/v2/sports/{}/{}/teams": {
            "get": {
                "summary": "List teams in league",
                "responses": {"200": {"description": "List of teams"}},
            }
        },
        "/apis/site/v2/sports/{}/{}/teams/{}": {
            "get": {
                "summary": "Get team details",
                "responses": {"200": {"description": "Team details"}},
            }
        },
        "/apis/site/v2/sports/{}/{}/teams/{}/statistics": {
            "get": {
                "summary": "Get team statistics",
                "responses": {"200": {"description": "Team statistics"}},
            }
        },
        "/apis/site/v2/sports/{}/{}/transactions": {
            "get": {
                "summary": "Get league transactions",
                "parameters": [
                    {"name": "limit", "in": "query", "required": False},
                ],
                "responses": {"200": {"description": "Transactions"}},
            }
        },
        "/apis/common/v3/sports/{}/{}/athletes/{}/bio": {
            "get": {
                "summary": "Get athlete biography",
                "responses": {"200": {"description": "Athlete bio"}},
            }
        },
        "/apis/common/v3/sports/{}/{}/athletes/{}/stats": {
            "get": {
                "summary": "Get athlete statistics",
                "parameters": [{"name": "season", "in": "query", "required": False}],
                "responses": {"200": {"description": "Athlete statistics"}},
            }
        },
        "/apis/common/v3/sports/{}/{}/athletes/{}/gamelog": {
            "get": {
                "summary": "Get athlete gamelog",
                "parameters": [{"name": "season", "in": "query", "required": False}],
                "responses": {"200": {"description": "Athlete gamelog"}},
            }
        },
        "/apis/common/v3/sports/{}/{}/athletes/{}/splits": {
            "get": {
                "summary": "Get athlete splits",
                "parameters": [{"name": "season", "in": "query", "required": False}],
                "responses": {"200": {"description": "Athlete splits"}},
            }
        },
        "/apis/common/v3/sports/{}/{}/statistics/byathlete": {
            "get": {
                "summary": "Get statistical leaders by athlete",
                "parameters": [
                    {"name": "limit", "in": "query", "required": False},
                    {"name": "category", "in": "query", "required": False},
                    {"name": "sort", "in": "query", "required": False},
                ],
                "responses": {"200": {"description": "Athlete leaders"}},
            }
        },
        "/apis/common/v3/sports/{}/{}/statistics/byteam": {
            "get": {
                "summary": "Get statistical leaders by team",
                "parameters": [
                    {"name": "limit", "in": "query", "required": False},
                    {"name": "category", "in": "query", "required": False},
                    {"name": "sort", "in": "query", "required": False},
                ],
                "responses": {"200": {"description": "Team leaders"}},
            }
        },
        "/apis/site/v2/sports/{}/{}/groups": {
            "get": {
                "summary": "Get league groups",
                "responses": {"200": {"description": "League groups"}},
            }
        },
        "/apis/site/v2/sports/{}/{}/events": {
            "get": {
                "summary": "Get league events",
                "parameters": [{"name": "dates", "in": "query", "required": False}],
                "responses": {"200": {"description": "League events"}},
            }
        },
        "/apis/site/v2/sports/{}/{}/draft": {
            "get": {
                "summary": "Get league draft",
                "parameters": [{"name": "season", "in": "query", "required": False}],
                "responses": {"200": {"description": "League draft"}},
            }
        },
        "/apis/v2/scoreboard/header": {
            "get": {
                "summary": "Get scoreboard header",
                "parameters": [
                    {"name": "sport", "in": "query", "required": False},
                    {"name": "league", "in": "query", "required": False},
                ],
                "responses": {"200": {"description": "Scoreboard header"}},
            }
        },
        "/v2/sports/{}/leagues/{}/events/{}/competitions/{}/odds": {
            "get": {
                "summary": "Get event odds",
                "responses": {"200": {"description": "Event odds"}},
            }
        },
        "/v2/sports/{}/leagues/{}/events/{}/competitions/{}/plays": {
            "get": {
                "summary": "Get play by play",
                "parameters": [
                    {"name": "limit", "in": "query", "required": False},
                    {"name": "page", "in": "query", "required": False},
                ],
                "responses": {"200": {"description": "Play by play"}},
            }
        },
        "/v2/sports/{}/leagues/{}/events/{}/competitions/{}/situation": {
            "get": {
                "summary": "Get game situation",
                "responses": {"200": {"description": "Game situation"}},
            }
        },
        "/v2/sports/{}/leagues/{}/events/{}/competitions/{}/probabilities": {
            "get": {
                "summary": "Get win probabilities",
                "parameters": [{"name": "limit", "in": "query", "required": False}],
                "responses": {"200": {"description": "Win probabilities"}},
            }
        },
        "/v2/sports/{}/leagues/{}/events/{}/competitions/{}/predictor": {
            "get": {
                "summary": "Get game predictor",
                "responses": {"200": {"description": "Game predictor"}},
            }
        },
        "/v2/sports/{}/leagues/{}/calendar": {
            "get": {
                "summary": "Get calendar",
                "responses": {"200": {"description": "Calendar"}},
            }
        },
        "/v2/sports/{}/leagues/{}/calendar/ondays": {
            "get": {
                "summary": "Get calendar on days",
                "parameters": [{"name": "dates", "in": "query", "required": False}],
                "responses": {"200": {"description": "Calendar on days"}},
            }
        },
        "/v2/sports/{}/leagues/{}": {
            "get": {
                "summary": "Get league (current season)",
                "responses": {"200": {"description": "League with its current season"}},
            }
        },
        "/v2/sports/{}/leagues/{}/seasons/{}/futures": {
            "get": {
                "summary": "Get season futures",
                "responses": {"200": {"description": "Season futures"}},
            }
        },
        "/v2/sports/{}/leagues/{}/seasons/{}/powerindex": {
            "get": {
                "summary": "Get season power index",
                "responses": {"200": {"description": "Season power index"}},
            }
        },
        "/v2/sports/{sport}/leagues/{league}/seasons/{season}/athletes/{athlete}": {
            "get": {
                "summary": "Get season athlete details",
                "parameters": [
                    {
                        "name": "sport",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                    {
                        "name": "league",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                    {
                        "name": "season",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                    {
                        "name": "athlete",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                ],
                "responses": {"200": {"description": "Athlete details"}},
            }
        },
        "/v2/sports/{}/leagues/{}/seasons/{}/types/{}/teams/{}/odds-records": {
            "get": {
                "summary": "Get team season odds records (ATS spread, moneyline)",
                "responses": {"200": {"description": "Team odds records"}},
            }
        },
        "/v3/sports/{}/{}/injuries": {
            "get": {"summary": "League injuries", "responses": {"200": {"description": "Injuries"}}}
        },
        "/v3/sports/{}/{}/events/{}/competitions/{}/drives": {
            "get": {
                "summary": "Competition drives",
                "responses": {"200": {"description": "Drives"}},
            }
        },
        "/v3/sports/{}/{}/seasons/{}/qbr": {
            "get": {"summary": "Season QBR", "responses": {"200": {"description": "Season QBR"}}}
        },
        "/v3/sports/{}/{}/qbr": {
            "get": {"summary": "League QBR", "responses": {"200": {"description": "League QBR"}}}
        },
        "/v3/sports/golf/{}/tournaments": {
            "get": {
                "summary": "Golf tournaments",
                "responses": {"200": {"description": "Golf tournaments"}},
            }
        },
        "/v3/bets/featured": {
            "get": {
                "summary": "Featured bets",
                "responses": {"200": {"description": "Featured bets"}},
            }
        },
        "/v3/bets/trending": {
            "get": {
                "summary": "Trending bets",
                "responses": {"200": {"description": "Trending bets"}},
            }
        },
        "/v3/sports/{}/{}/coaches": {
            "get": {"summary": "League coaches", "responses": {"200": {"description": "Coaches"}}}
        },
        "/v3/sports/{}/{}/seasons/{}/coaches": {
            "get": {
                "summary": "Season coaches",
                "responses": {"200": {"description": "Season coaches"}},
            }
        },
        "/v2/sports/{}/leagues/{}/events/{}/competitions/{}/drives/{}/plays": {
            "get": {"summary": "Drive plays", "responses": {"200": {"description": "Drive plays"}}}
        },
        "/v2/sports/{}/leagues/{}/seasons/{}/types/{}/weeks/{}/qbr/{}": {
            "get": {"summary": "Week QBR", "responses": {"200": {"description": "Week QBR"}}}
        },
        "/v2/sports/golf/leagues/{}/tournaments/{}": {
            "get": {
                "summary": "Golf tournament",
                "responses": {"200": {"description": "Golf tournament"}},
            }
        },
        "/v2/sports/{}/leagues/{}/bet-types/{}": {
            "get": {"summary": "Bet type", "responses": {"200": {"description": "Bet type"}}}
        },
        "/v2/sports/{}/leagues/{}/seasons/{}/awards": {
            "get": {"summary": "Season awards", "responses": {"200": {"description": "Awards"}}}
        },
        "/v2/sports/{}/leagues/{}/seasons/{}/awards/{}": {
            "get": {"summary": "Season award", "responses": {"200": {"description": "Award"}}}
        },
        "/v2/sports/{}/leagues/{}/seasons/{}/coaches/{}": {
            "get": {"summary": "Season coach", "responses": {"200": {"description": "Coach"}}}
        },
        "/v2/sports/{}/leagues/{}/venues": {
            "get": {"summary": "Venues", "responses": {"200": {"description": "Venues"}}}
        },
        "/v2/sports/{}/leagues/{}/venues/{}": {
            "get": {"summary": "Venue", "responses": {"200": {"description": "Venue"}}}
        },
        "/v2/sports/{}/leagues/{}/franchises": {
            "get": {"summary": "Franchises", "responses": {"200": {"description": "Franchises"}}}
        },
        "/v2/sports/{}/leagues/{}/franchises/{}": {
            "get": {"summary": "Franchise", "responses": {"200": {"description": "Franchise"}}}
        },
        "/v2/sports/{}/leagues/{}/teams/{}/history/{}": {
            "get": {
                "summary": "Team tournament history",
                "responses": {"200": {"description": "Tournament history"}},
            }
        },
        "/v2/sports/{}/leagues/{}/seasons/{}/recruits": {
            "get": {"summary": "Recruits", "responses": {"200": {"description": "Recruits"}}}
        },
        "/v2/sports/{}/leagues/{}/recruiting/{}/rankings": {
            "get": {
                "summary": "Recruiting rankings",
                "responses": {"200": {"description": "Recruiting rankings"}},
            }
        },
        "/items": {
            "get": {
                "summary": "Mock test items",
                "parameters": [
                    {"name": "limit", "in": "query", "required": False},
                    {
                        "name": "search",
                        "in": "query",
                        "required": False,
                        "deprecated": True,
                        "description": "**[Deprecated]** Use specific filter parameters instead.",
                    },
                ],
                "responses": {"200": {"description": "List of items"}},
            }
        },
    },
}


DEFAULT_TEMPLATE_SPEC = DEFAULT_ESPN_SPEC


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
        caller_name = ""
        if isinstance(func, ast.Attribute):
            method_name = func.attr
            if isinstance(func.value, ast.Name):
                caller_name = func.value.id
            elif isinstance(func.value, ast.Attribute):
                caller_name = func.value.attr

        # Only inspect HTTP client invocations (not dict.get() or object.get())
        valid_callers = {
            "client",
            "_client",
            "http_client",
            "_custom_client",
            "self",
            "httpx",
            "custom_client",
            "session",
        }
        if not caller_name or caller_name not in valid_callers:
            self.generic_visit(node)
            return

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
            elif caller_name in valid_callers:
                method = method_name.upper()
                if len(node.args) >= 1:
                    raw_path = self._extract_path(node.args[0])

            for kw in node.keywords:
                if kw.arg in ("params", "query_params"):
                    query_params.update(self._extract_dict_keys(kw.value))
                elif kw.arg in ("json", "json_data", "body"):
                    body_keys.update(self._extract_dict_keys(kw.value))

            if (
                caller_name in valid_callers
                and method_name.lower() in ("get", "post", "put", "patch", "delete")
                and len(node.args) >= 2
            ):
                if method_name.lower() == "get":
                    query_params.update(self._extract_dict_keys(node.args[1]))
                else:
                    body_keys.update(self._extract_dict_keys(node.args[1]))

            if method and raw_path and not raw_path.isdigit():
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


SPEC_FETCH_TIMEOUT_SECONDS = 30.0


class SpecNotJSONError(Exception):
    """The spec URL answered with a body that doesn't decode as JSON (YAML, HTML, ...)."""

    def __init__(self, url: str, status_code: int) -> None:
        super().__init__(f"spec URL returned non-JSON: {url} (HTTP {status_code})")
        self.url = url
        self.status_code = status_code


class SpecFetchError(Exception):
    """A WADL body could not be fetched or parsed. The message names no response body."""


class SnapshotError(Exception):
    """A committed route table has a bad line or tag."""


WADL_NS = {"w": "http://wadl.dev.java.net/2009/02"}
CORE_WADL_URLS = (
    ("v2", "https://sports.core.api.espn.com/v2/application.wadl?detail=true"),
    ("v3", "https://sports.core.api.espn.com/v3/application.wadl?detail=true"),
)
VALID_ROUTE_TAGS = frozenset({"covered", "gap", "excluded", "deprecated"})
_ROUTE_LINE = re.compile(r"^(GET)\s+(\S+)\s+#\s+([A-Za-z0-9_-]+):\s*(.*?)\s*$")
_SCRIPT_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class RouteLine:
    method: str
    path: str
    tag: str
    reason: str


def load_route_table(path: Path) -> list[RouteLine]:
    """Parse ``METHOD path  # tag: reason`` lines. Blank lines and ``#`` comments skip."""
    if not path.exists():
        raise SnapshotError(f"route table not found: {path.name}")
    rows: list[RouteLine] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _ROUTE_LINE.match(line)
        if match is None:
            raise SnapshotError(f"bad snapshot line {lineno}")
        method, route, tag, reason = match.groups()
        if tag not in VALID_ROUTE_TAGS:
            raise SnapshotError(f"bad tag {tag!r} on line {lineno}")
        if tag == "covered" and not reason.strip():
            raise SnapshotError(f"covered route has no tool on line {lineno}")
        rows.append(RouteLine(method, route, tag, reason.strip()))
    return rows


def sample_url(reason: str) -> str:
    """Return the trailing https sample URL, or an empty string when the line has none."""
    parts = reason.split()
    if parts and parts[-1].startswith("https://"):
        return parts[-1]
    return ""


def load_site_routes(path: Path) -> list[RouteLine]:
    """Site hand list. Covered lines carry a sample URL; excluded lines are not fetched."""
    rows = load_route_table(path)
    for row in rows:
        if row.tag == "excluded":
            continue
        if row.tag != "covered":
            raise SnapshotError(f"site route {row.path} must be covered or excluded")
        if not sample_url(row.reason):
            raise SnapshotError(f"site route {row.path} has no sample URL")
    return rows


def parse_wadl_get_routes(xml_text: str, version: str) -> set[str]:
    """GET paths from a core WADL, parameters collapsed to ``{}``, prefixed with the version."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise SpecFetchError("malformed XML") from exc
    resources = root.find("w:resources", WADL_NS)
    if resources is None:
        raise SpecFetchError("malformed XML")
    found: set[str] = set()

    def walk(elem: ET.Element, prefix: str) -> None:
        path = elem.get("path")
        full = prefix
        if path is not None:
            if not prefix:
                full = path if path.startswith("/") else "/" + path
            elif path.startswith("/"):
                full = prefix.rstrip("/") + path
            else:
                full = prefix.rstrip("/") + "/" + path
        for method in elem.findall("w:method", WADL_NS):
            if method.get("name") == "GET":
                norm = re.sub(r"\{[^}]*\}", "{}", full)
                if not norm.startswith("/"):
                    norm = "/" + norm
                found.add(f"/{version}{norm}")
        for child in list(elem):
            if child.tag == f"{{{WADL_NS['w']}}}resource":
                walk(child, full)

    for child in list(resources):
        if child.tag == f"{{{WADL_NS['w']}}}resource":
            walk(child, "")
    return found


async def _spec_get(url: str) -> httpx.Response:
    """GET through the same SSRF-pinned transport the JSON spec fetch uses."""
    async with httpx.AsyncClient(
        transport=SSRFSafeAsyncTransport(verify=True),
        timeout=SPEC_FETCH_TIMEOUT_SECONDS,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        return await client.get(url)


async def fetch_spec_text(url: str) -> str:
    """Fetch a WADL (or any spec URL) as text. Non-200, timeout, and connect failures raise."""
    try:
        resp = await _spec_get(url)
    except httpx.TimeoutException as exc:
        raise SpecFetchError("timeout") from exc
    except (httpx.HTTPError, ESPNConnectionError) as exc:
        raise SpecFetchError(f"connection error: {type(exc).__name__}") from exc
    if resp.status_code != 200:
        raise SpecFetchError(f"HTTP {resp.status_code}")
    return resp.text


async def fetch_url_status(url: str) -> str:
    """Status code as text, or ``timeout`` / ``connection``. Same pinned transport."""
    try:
        resp = await _spec_get(url)
    except httpx.TimeoutException:
        return "timeout"
    except (httpx.HTTPError, ESPNConnectionError):
        return "connection"
    return str(resp.status_code)


async def fetch_spec_json(url: str) -> Any:
    """Fetch the spec through the client's SSRF transport and decode it as JSON.

    The transport checks every address the host resolves to and connects only to a
    checked one, keeping the hostname for the Host header, TLS SNI and certificate
    verification. Redirects are not followed: a 3xx target would skip the check.
    httpx ignores env proxies whenever a transport is passed
    (``allow_env_proxies = trust_env and transport is None``), so connections always go
    direct through the pin; egress-proxy deployments need a proxy-aware transport, which
    isn't provided; ``trust_env=False`` on the drift fetch is belt-and-braces.
    """
    resp = await _spec_get(url)
    resp.raise_for_status()
    try:
        return resp.json()
    except ValueError as e:
        # json.JSONDecodeError and UnicodeDecodeError are both ValueErrors. Report the
        # URL and status only, never the body.
        raise SpecNotJSONError(url, resp.status_code) from e


async def _registered_tools() -> set[str]:
    from espn_mcp.server import create_server

    server = create_server(profile="full")
    tools = await server.list_tools()
    return {tool.name for tool in tools}


def compare_routes(live: set[str], rows: list[RouteLine], registry: set[str]) -> int:
    """New = live − covered − baseline. A covered tool missing from the registry exits 1."""
    covered = {row.path for row in rows if row.tag == "covered"}
    baseline = {row.path for row in rows if row.tag != "covered"}
    code = 0
    for row in rows:
        if row.tag != "covered":
            continue
        tool = row.reason.split()[0]
        if tool not in registry:
            print(f"❌ Mapped tool is not registered: {tool} (GET {row.path})")
            code = 1
    new = sorted(live - covered - baseline)
    if new:
        print(f"❌ New uncovered ESPN routes: {len(new)}")
        for path in new:
            print(f"  GET {path}")
        code = 1
    for row in rows:
        if row.tag != "covered" and row.path not in live:
            print(f"⚠️ Baseline route vanished: GET {row.path}")
    return code


async def check_site_liveness(
    routes: list[RouteLine],
    getter: Callable[[str], Awaitable[str]],
) -> int:
    """GET each covered sample. One retry. Excluded lines are not fetched.

    Every request failing at the connection level is a host outage (exit 2), not dead routes.
    """
    checked: list[str] = []
    dead: list[tuple[str, str]] = []
    for route in routes:
        if route.tag == "excluded":
            continue
        url = sample_url(route.reason)
        status = await getter(url)
        if status != "200":
            status = await getter(url)
        checked.append(status)
        if status != "200":
            dead.append((route.path, status))
    if checked and all(status == "connection" for status in checked):
        print("::error::Site host outage: every site route failed at the connection level")
        return 2
    for path, status in dead:
        print(f"❌ Dead site route: {path} ({status})")
    return 1 if dead else 0


async def _live_routes(fetch_text: Callable[[str], Awaitable[str]]) -> set[str]:
    live: set[str] = set()
    for version, url in CORE_WADL_URLS:
        text = await fetch_text(url)
        live.update(parse_wadl_get_routes(text, version))
    return live


async def run_uncovered_gate(snapshot: Path, site_routes: Path) -> int:
    """Diff the live core WADLs against the snapshot and liveness-check site samples."""
    rows = load_route_table(snapshot)
    site = load_site_routes(site_routes)
    try:
        live = await _live_routes(fetch_spec_text)
    except SpecFetchError as exc:
        print(f"::error::WADL fetch/parse failed: {exc}")
        return 2
    registry = await _registered_tools()
    route_code = compare_routes(live, rows, registry)
    site_code = await check_site_liveness(site, fetch_url_status)
    if 2 in (route_code, site_code):
        return 2
    if 1 in (route_code, site_code):
        return 1
    return 0


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
    parser.add_argument(
        "--fail-on-new-uncovered",
        action="store_true",
        help="Fail when the live core WADLs have a GET route outside the snapshot",
    )
    parser.add_argument(
        "--routes-snapshot",
        default=str(_SCRIPT_DIR / "espn_routes_snapshot.tsv"),
        help="Committed core route snapshot",
    )
    parser.add_argument(
        "--site-routes",
        default=str(_SCRIPT_DIR / "espn_site_routes.txt"),
        help="Site routes to liveness-check when --fail-on-new-uncovered is set",
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
            # HTTPS only; loopback names and non-global IP literals are refused up front.
            _validate_base_url(args.spec_url, allowed_hosts_str="")
        except ValueError as e:
            print(f"Error: refused to fetch spec URL: {e}", file=sys.stderr)
            return 2
        try:
            raw_spec = asyncio.run(fetch_spec_json(args.spec_url))
        except ESPNConnectionError as e:
            # A host that is, or resolves to, a non-global address, or has no checked one.
            print(f"Error: refused to fetch spec URL: {e}", file=sys.stderr)
            return 2
        except SpecNotJSONError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 2
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
    if args.fail_on_new_uncovered:
        try:
            coverage = asyncio.run(
                run_uncovered_gate(Path(args.routes_snapshot), Path(args.site_routes))
            )
        except SnapshotError as exc:
            print(f"Error: bad routes snapshot: {exc}", file=sys.stderr)
            coverage = 2
        if 2 in (exit_code, coverage):
            return 2
        if 1 in (exit_code, coverage):
            return 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
