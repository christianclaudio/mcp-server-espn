"""Settings parsing: the Tool Search backend env is case- and space-insensitive."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from espn_mcp.config import Settings

ENV = "ESPN_MCP_TOOL_SEARCH_BACKEND"


def test_tool_search_backend_defaults_to_bm25(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV, raising=False)
    assert Settings().MCP_TOOL_SEARCH_BACKEND == "bm25"


def test_tool_search_backend_env_selects_regex(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV, "regex")
    assert Settings().MCP_TOOL_SEARCH_BACKEND == "regex"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("BM25", "bm25"), (" bm25", "bm25"), (" BM25 ", "bm25"), ("Regex\n", "regex")],
)
def test_tool_search_backend_env_is_normalized(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: str
) -> None:
    monkeypatch.setenv(ENV, raw)
    assert Settings().MCP_TOOL_SEARCH_BACKEND == expected


def test_tool_search_backend_unknown_value_still_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV, "fuzzy")
    with pytest.raises(ValidationError, match="'regex' or 'bm25'"):
        Settings()


def test_tool_search_backend_non_string_passes_through() -> None:
    with pytest.raises(ValidationError):
        Settings(MCP_TOOL_SEARCH_BACKEND=3)  # type: ignore[arg-type]
