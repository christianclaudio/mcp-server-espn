"""Tests for determine_bump.py SemVer calculation logic."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scripts.determine_bump import (
    analyze_commits,
    get_commits_since_tag,
    get_current_version,
    get_latest_tag,
    increment_semver,
    main,
)


def test_increment_semver() -> None:
    """Verify SemVer increment calculation across major, minor, and patch."""
    assert increment_semver("1.2.3", "MAJOR") == "2.0.0"
    assert increment_semver("1.2.3", "MINOR") == "1.3.0"
    assert increment_semver("1.2.3", "PATCH") == "1.2.4"
    assert increment_semver("1.2.3", "NONE") == "1.2.3"
    assert increment_semver("invalid", "PATCH") == "invalid"


def test_analyze_commits_breaking() -> None:
    """Verify breaking commits trigger MAJOR bump."""
    commits = [
        "feat!: remove legacy transport",
        "fix: small typo",
    ]
    rec = analyze_commits(commits, "1.0.0")
    assert rec.bump_type == "MAJOR"
    assert rec.suggested_version == "2.0.0"
    assert len(rec.breaking_commits) == 1
    assert len(rec.fix_commits) == 1

    commits_breaking_change = [
        "refactor: update core API\n\nBREAKING CHANGE: changes return signature",
    ]
    rec2 = analyze_commits(commits_breaking_change, "1.0.0")
    assert rec2.bump_type == "MAJOR"
    assert rec2.suggested_version == "2.0.0"


def test_analyze_commits_features() -> None:
    """Verify feature commits trigger MINOR bump."""
    commits = [
        "feat(tools): add get_summary endpoint",
        "fix: resolve timeout",
    ]
    rec = analyze_commits(commits, "1.1.0")
    assert rec.bump_type == "MINOR"
    assert rec.suggested_version == "1.2.0"
    assert len(rec.feat_commits) == 1
    assert len(rec.fix_commits) == 1


def test_analyze_commits_fixes() -> None:
    """Verify fix and perf commits trigger PATCH bump."""
    commits = [
        "fix: handle 404 cleanly",
        "perf: optimize connection pool",
    ]
    rec = analyze_commits(commits, "1.1.2")
    assert rec.bump_type == "PATCH"
    assert rec.suggested_version == "1.1.3"
    assert len(rec.fix_commits) == 2


def test_analyze_commits_none() -> None:
    """Verify chore and docs commits trigger NONE bump."""
    commits = [
        "chore: update dependencies",
        "docs: clarify runbook",
    ]
    rec = analyze_commits(commits, "1.1.0")
    assert rec.bump_type == "NONE"
    assert rec.suggested_version == "1.1.0"
    assert len(rec.other_commits) == 2


def test_get_current_version(tmp_path: Path) -> None:
    """Verify version extraction from pyproject.toml."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('version = "2.3.4"\n', encoding="utf-8")
    assert get_current_version(tmp_path) == "2.3.4"

    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    assert get_current_version(empty_dir) == "0.0.0"


def test_get_latest_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify get_latest_tag handles subprocess results and errors."""
    mock_run = MagicMock()
    mock_run.return_value = MagicMock(stdout="v1.1.0\n")
    monkeypatch.setattr(subprocess, "run", mock_run)
    assert get_latest_tag() == "v1.1.0"

    mock_run.side_effect = subprocess.CalledProcessError(1, "git")
    assert get_latest_tag() is None


def test_get_commits_since_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify get_commits_since_tag parses output lines and handles failures."""
    mock_run = MagicMock()
    mock_run.return_value = MagicMock(stdout="feat: one\x1efix: two\x1e")
    monkeypatch.setattr(subprocess, "run", mock_run)
    assert get_commits_since_tag("v1.0.0") == ["feat: one", "fix: two"]

    mock_run.side_effect = subprocess.CalledProcessError(1, "git")
    assert get_commits_since_tag("v1.0.0") == []


def test_main_cli(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Verify CLI entrypoint with both standard and JSON output."""
    monkeypatch.setattr("sys.argv", ["determine_bump.py"])
    code = main()
    assert code == 0
    captured = capsys.readouterr()
    assert "SemVer Release Bump Recommendation" in captured.out

    monkeypatch.setattr("sys.argv", ["determine_bump.py", "--json"])
    code_json = main()
    assert code_json == 0
    captured_json = capsys.readouterr()
    assert '"bump_type"' in captured_json.out
