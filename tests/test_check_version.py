"""Tests for scripts/check_version.py (reads the built wheel; rejects 0.0.0 and 0.0.1.devN)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from scripts import check_version


def make_wheel(
    dist: Path,
    version: str | None,
    *,
    name: str = "demo_pkg",
    metadata: bool = True,
    extra_lines: str = "",
) -> Path:
    """Write a minimal wheel: a zip whose ``*.dist-info/METADATA`` carries ``version``."""
    dist.mkdir(parents=True, exist_ok=True)
    label = version or "0"
    wheel = dist / f"{name}-{label}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(f"{name}/__init__.py", "")
        if metadata:
            body = "Metadata-Version: 2.4\nName: demo-pkg\n"
            if version is not None:
                body += f"Version: {version}\n"
            archive.writestr(f"{name}-{label}.dist-info/METADATA", body + extra_lines)
    return wheel


@pytest.mark.parametrize(
    ("found", "allow_untagged", "ok"),
    [
        ("1.3.0", False, True),
        ("1.3.1.dev2+g22ddd38", False, True),
        ("1.3.1.dev2+g22ddd38", True, True),
        ("0.0.1.dev1+gabc1234", False, False),
        ("0.0.1.dev1+gabc1234", True, True),
        ("0.0.1.dev3", False, False),
        ("0.0.1", False, True),
        ("0.0.0", False, False),
        ("0.0.0", True, False),
    ],
)
def test_problem(found: str, allow_untagged: bool, ok: bool) -> None:
    assert (check_version.problem(found, allow_untagged=allow_untagged) is None) is ok


def test_problem_default_rejects_untagged() -> None:
    assert check_version.problem("0.0.1.dev1+gabc1234") is not None


def test_main_passes_on_real_dev_version(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wheel = make_wheel(tmp_path / "dist", "1.3.1.dev5+247777e")
    assert check_version.main(["--dist-dir", str(tmp_path / "dist")]) == 0
    assert capsys.readouterr().out == f"{wheel.name}: version 1.3.1.dev5+247777e\n"


def test_main_defaults_to_dist_in_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    wheel = make_wheel(tmp_path / "dist", "1.3.1")
    monkeypatch.chdir(tmp_path)
    assert check_version.main([]) == 0
    assert capsys.readouterr().out == f"{wheel.name}: version 1.3.1\n"


def test_main_fails_on_fallback(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    wheel = make_wheel(tmp_path / "dist", "0.0.0")
    assert check_version.main(["--dist-dir", str(tmp_path / "dist")]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith(f"error: {wheel.name} version is the fallback 0.0.0")
    assert "fetch-depth: 0" in captured.err


def test_main_fails_on_untagged_by_default(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wheel = make_wheel(tmp_path / "dist", "0.0.1.dev1+gabc1234")
    assert check_version.main(["--dist-dir", str(tmp_path / "dist")]) == 1
    err = capsys.readouterr().err
    assert err.startswith(f"error: {wheel.name} version 0.0.1.dev1+gabc1234")
    assert "no v* tag is reachable" in err


def test_main_allow_untagged_accepts_untagged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wheel = make_wheel(tmp_path / "dist", "0.0.1.dev1+gabc1234")
    assert check_version.main(["--dist-dir", str(tmp_path / "dist"), "--allow-untagged"]) == 0
    assert capsys.readouterr().out == f"{wheel.name}: version 0.0.1.dev1+gabc1234\n"


@pytest.mark.parametrize("count", [0, 2])
def test_main_needs_exactly_one_wheel(
    tmp_path: Path, count: int, capsys: pytest.CaptureFixture[str]
) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "demo_pkg-1.3.1.tar.gz").write_bytes(b"")
    for i in range(count):
        make_wheel(dist, f"1.3.{i}")
    assert check_version.main(["--dist-dir", str(dist)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert f"expected exactly one wheel in {dist}/, found {count}" in captured.err


def test_main_missing_dist_dir(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert check_version.main(["--dist-dir", str(tmp_path / "nope")]) == 1
    assert "found 0" in capsys.readouterr().err


def test_main_fails_without_metadata(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    wheel = make_wheel(tmp_path / "dist", "1.3.1", metadata=False)
    assert check_version.main(["--dist-dir", str(tmp_path / "dist")]) == 1
    assert capsys.readouterr().err == f"error: {wheel.name} has no *.dist-info/METADATA\n"


def test_main_fails_without_version_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wheel = make_wheel(tmp_path / "dist", None)
    assert check_version.main(["--dist-dir", str(tmp_path / "dist")]) == 1
    assert capsys.readouterr().err == f"error: {wheel.name} METADATA has no Version: line\n"


def test_main_fails_on_corrupt_wheel(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "demo_pkg-1.3.1-py3-none-any.whl").write_bytes(b"not a zip")
    assert check_version.main(["--dist-dir", str(dist)]) == 1
    assert "is not a valid wheel (zip) file" in capsys.readouterr().err


def test_wheel_version_takes_first_version_line(tmp_path: Path) -> None:
    wheel = make_wheel(tmp_path / "dist", "1.3.1", extra_lines="\nVersion: 9.9.9\n")
    assert check_version.wheel_version(wheel) == "1.3.1"
