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
    get_repo_root,
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

    commits_breaking_hyphen = [
        "refactor: update core API\n\nBREAKING-CHANGE: changes return signature",
    ]
    rec3 = analyze_commits(commits_breaking_hyphen, "1.0.0")
    assert rec3.bump_type == "MAJOR"
    assert rec3.suggested_version == "2.0.0"


def test_analyze_commits_breaking_prose_regression() -> None:
    """Verify prose mentioning BREAKING CHANGE outside footer does not trigger MAJOR bump."""
    commits = [
        "docs: explain BREAKING CHANGE: footer syntax in contributing guide",
        "fix: correct note about BREAKING-CHANGE: parser behavior",
    ]
    rec = analyze_commits(commits, "1.0.0")
    assert rec.bump_type == "PATCH"
    assert rec.suggested_version == "1.0.1"
    assert len(rec.breaking_commits) == 0
    assert len(rec.fix_commits) == 1
    assert len(rec.other_commits) == 1


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

    pyproject_zero = tmp_path / "zero"
    pyproject_zero.mkdir()
    (pyproject_zero / "pyproject.toml").write_text('version = "0.0.0"\n', encoding="utf-8")
    assert get_current_version(pyproject_zero) == "0.0.0"

    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    assert get_current_version(empty_dir) is None

    no_ver_dir = tmp_path / "no_ver"
    no_ver_dir.mkdir()
    (no_ver_dir / "pyproject.toml").write_text('description = "test"\n', encoding="utf-8")
    assert get_current_version(no_ver_dir) is None


def test_get_latest_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify get_latest_tag handles subprocess results and errors."""
    mock_run = MagicMock()
    mock_run.return_value = MagicMock(stdout="v1.1.0\n")
    monkeypatch.setattr(subprocess, "run", mock_run)
    assert get_latest_tag() == "v1.1.0"

    mock_run.side_effect = subprocess.CalledProcessError(
        128, "git", stderr="fatal: No names found, cannot describe anything."
    )
    assert get_latest_tag() is None

    mock_run.side_effect = subprocess.CalledProcessError(
        1, "git", stderr="fatal: No tags can describe"
    )
    assert get_latest_tag() is None

    mock_run.side_effect = subprocess.CalledProcessError(
        2, "git", stderr="fatal: repository corrupted"
    )
    with pytest.raises(subprocess.CalledProcessError):
        get_latest_tag()


def test_get_commits_since_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify get_commits_since_tag parses output lines and handles failures."""
    mock_run = MagicMock()
    mock_run.return_value = MagicMock(stdout="feat: one\x1efix: two\x1e")
    monkeypatch.setattr(subprocess, "run", mock_run)
    assert get_commits_since_tag("v1.0.0") == ["feat: one", "fix: two"]

    mock_run.side_effect = subprocess.CalledProcessError(1, "git")
    with pytest.raises(subprocess.CalledProcessError):
        get_commits_since_tag("v1.0.0")


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


def test_main_cli_git_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify CLI exits with code 1 when git command fails."""
    monkeypatch.setattr("sys.argv", ["determine_bump.py"])

    def mock_get_latest_tag() -> str | None:
        raise subprocess.CalledProcessError(1, "git", stderr="fatal: not a git repo")

    monkeypatch.setattr("scripts.determine_bump.get_latest_tag", mock_get_latest_tag)
    code = main()
    assert code == 1
    captured = capsys.readouterr()
    assert "Error executing git command" in captured.err


def test_main_cli_all_commit_categories(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify CLI prints all commit categories (breaking, features, fixes, others)."""
    monkeypatch.setattr("sys.argv", ["determine_bump.py"])
    monkeypatch.setattr("scripts.determine_bump.get_latest_tag", lambda: "v1.0.0")
    monkeypatch.setattr(
        "scripts.determine_bump.get_commits_since_tag",
        lambda _tag: [
            "feat!: breaking change",
            "feat: new feature",
            "fix: bug fix",
            "chore: update deps",
        ],
    )
    code = main()
    assert code == 0
    captured = capsys.readouterr()
    assert "Breaking changes (1):" in captured.out
    assert "Features (1):" in captured.out
    assert "Fixes & perf (1):" in captured.out
    assert "Maintenance/other (1):" in captured.out


def test_main_cli_empty_commits(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify CLI prints when no commits since tag."""
    monkeypatch.setattr("sys.argv", ["determine_bump.py"])
    monkeypatch.setattr("scripts.determine_bump.get_latest_tag", lambda: None)
    monkeypatch.setattr("scripts.determine_bump.get_commits_since_tag", lambda _tag: [])
    code = main()
    assert code == 0
    captured = capsys.readouterr()
    assert "None (initial release)" in captured.out


def test_get_repo_root_git_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify get_repo_root returns toplevel path when git rev-parse succeeds."""
    mock_run = MagicMock()
    mock_run.return_value = MagicMock(stdout=f"{tmp_path}\n")
    monkeypatch.setattr(subprocess, "run", mock_run)
    assert get_repo_root() == tmp_path


def test_get_repo_root_fallback_pyproject(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify get_repo_root falls back to parent directory containing pyproject.toml."""
    mock_run = MagicMock(side_effect=subprocess.CalledProcessError(1, "git"))
    monkeypatch.setattr(subprocess, "run", mock_run)

    project_dir = tmp_path / "project"
    sub_dir = project_dir / "src" / "pkg"
    sub_dir.mkdir(parents=True)
    (project_dir / "pyproject.toml").write_text("version = '1.0.0'\n", encoding="utf-8")

    monkeypatch.setattr(Path, "cwd", lambda: sub_dir)
    assert get_repo_root() == project_dir


def test_get_repo_root_fallback_no_pyproject(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verify get_repo_root falls back to cwd when no pyproject.toml exists in ancestry."""
    mock_run = MagicMock(side_effect=subprocess.CalledProcessError(1, "git"))
    monkeypatch.setattr(subprocess, "run", mock_run)

    empty_dir = tmp_path / "isolated"
    empty_dir.mkdir()
    monkeypatch.setattr(Path, "cwd", lambda: empty_dir)
    assert get_repo_root() == empty_dir


def test_main_cli_missing_pyproject(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify CLI exits with code 1 when pyproject.toml cannot provide a valid version."""
    monkeypatch.setattr("sys.argv", ["determine_bump.py"])
    monkeypatch.setattr("scripts.determine_bump.get_current_version", lambda _root: None)
    code = main()
    assert code == 1
    captured = capsys.readouterr()
    assert "Error: Could not locate pyproject.toml with a valid version" in captured.err
