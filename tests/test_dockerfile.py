"""Dockerfile trust-store ordering, the ca-certificates floor, the hash-pinned pip and uv
bootstraps, and the locked dependency install.

pip 24.2+ on Python 3.10+ trusts the OS certificate store as well as its bundled certifi, so
the builder must update ``ca-certificates`` and pass the floor check before any pip runs, and
the pip bootstrap must not depend on TLS trust at all (``--require-hashes``). Runtime dependencies
come from ``uv.lock`` (``uv export`` into a hashed requirements file), so the image runs the
versions CI tests and nothing resolves them at build time. The project build backend
(``docker/build-backend.txt``) is installed the same way, to ``/tmp/build-backend`` outside the
venv, and is on ``PYTHONPATH`` only for ``pip install --no-deps --no-build-isolation .``, so the
runtime image, which copies only the venv, gets no build backend.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

from scripts import gen_build_backend

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
BOOTSTRAP = (ROOT / "docker" / "pip-bootstrap.txt").read_text(encoding="utf-8")
UV_BOOTSTRAP = (ROOT / "docker" / "uv-bootstrap.txt").read_text(encoding="utf-8")
BUILD_BACKEND = (ROOT / "docker" / "build-backend.txt").read_text(encoding="utf-8")
FLOOR_CHECK = "dpkg --compare-versions"


def _logical_lines(text: str) -> list[str]:
    """Dockerfile instructions with backslash continuations joined and comments dropped."""
    out: list[str] = []
    buf = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not buf and (not line or line.startswith("#")):
            continue
        if line.endswith("\\"):
            buf += line[:-1].strip() + " "
            continue
        out.append(buf + line)
        buf = ""
    return out


def _stages() -> dict[str, list[str]]:
    """Instructions per named stage; key ``""`` holds the global ones before the first FROM."""
    stages: dict[str, list[str]] = {"": []}
    current = ""
    for line in _logical_lines(DOCKERFILE):
        match = re.match(r"FROM\s+\S+\s+AS\s+(\S+)$", line, flags=re.IGNORECASE)
        if match:
            current = match.group(1)
            stages[current] = []
        stages[current].append(line)
    return stages


def _floor_default() -> str:
    (default,) = [
        line.split("=", 1)[1]
        for line in _stages()[""]
        if line.startswith("ARG CA_CERTIFICATES_FLOOR=")
    ]
    return default


def _ca_run(stage: str) -> str:
    (run,) = [line for line in _stages()[stage] if line.startswith("RUN ") and FLOOR_CHECK in line]
    return run.removeprefix("RUN ")


def test_both_stages_use_the_same_base_image() -> None:
    """Both stages pin python:3.12-slim-trixie@sha256:<64 hex> to the same index digest."""
    froms = [lines[0] for name, lines in _stages().items() if name]
    images = [line.split()[1] for line in froms]
    assert len(froms) == 2
    pattern = re.compile(r"python:3\.12-slim-trixie@(sha256:[0-9a-f]{64})")
    digests = []
    for image in images:
        match = pattern.fullmatch(image)
        assert match is not None
        digests.append(match.group(1))
    assert digests[0] == digests[1]


def test_floor_is_declared_once_and_used_by_both_stages() -> None:
    assert _floor_default() == "20250419+deb13u1"
    for stage in ("builder", "runtime"):
        assert "ARG CA_CERTIFICATES_FLOOR" in _stages()[stage]
        assert '"$CA_CERTIFICATES_FLOOR"' in _ca_run(stage)
    assert _ca_run("builder") == _ca_run("runtime")


def test_builder_updates_ca_certificates_before_any_pip() -> None:
    builder = _stages()["builder"]
    ca_index = builder.index("RUN " + _ca_run("builder"))
    pip_runs = [i for i, line in enumerate(builder) if line.startswith("RUN ") and "pip" in line]
    venv_runs = [i for i, line in enumerate(builder) if "-m venv" in line]
    assert pip_runs
    assert ca_index < min(pip_runs + venv_runs)


def test_pip_bootstrap_is_hash_pinned() -> None:
    (bootstrap,) = [
        line for line in _stages()["builder"] if "pip-bootstrap.txt" in line and "RUN" in line
    ]
    assert "--require-hashes" in bootstrap
    assert "--only-binary=:all:" in bootstrap
    assert "legacy-certs" not in DOCKERFILE
    pins = re.findall(
        r"^pip==(\S+)\s*\\\n\s+--hash=sha256:([0-9a-f]{64})$", BOOTSTRAP, re.MULTILINE
    )
    assert len(pins) == 1
    version = tuple(int(part) for part in pins[0][0].split("."))
    assert version >= (26, 2, 2)


@pytest.mark.skipif(shutil.which("dpkg") is None, reason="needs dpkg --compare-versions")
@pytest.mark.parametrize("stage", ["builder", "runtime"])
@pytest.mark.parametrize(
    ("installed", "passes"),
    [
        ("20250419", False),
        ("20241223", False),
        ("20250419+deb13u1", True),
        ("20250419+deb13u2", True),
        ("20260101", True),
    ],
)
def test_ca_floor_check_fails_below_the_floor(
    tmp_path: Path, stage: str, installed: str, passes: bool
) -> None:
    """Run the stage's exact RUN command with apt-get, rm and dpkg-query stubbed; dpkg is real."""
    for name, body in {
        "apt-get": "exit 0",
        "rm": "exit 0",
        "dpkg-query": f"printf '%s' '{installed}'",
    }.items():
        stub = tmp_path / name
        stub.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        stub.chmod(0o755)
    env = {
        "PATH": f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}",
        "CA_CERTIFICATES_FLOOR": _floor_default(),
    }
    result = subprocess.run(  # noqa: S603 - the command is the repo's own Dockerfile RUN line
        ["/bin/sh", "-c", _ca_run(stage)], env=env, capture_output=True, text=True, check=False
    )
    assert (result.returncode == 0) is passes, result.stderr
    if not passes:
        assert f"ca-certificates {installed} is older than the floor" in result.stderr


def _builder_install() -> str:
    (run,) = [
        line for line in _stages()["builder"] if "pip-bootstrap.txt" in line and "RUN" in line
    ]
    return run


def test_builder_copies_the_lock_and_bootstrap_files() -> None:
    sources = [
        source
        for line in _stages()["builder"]
        if line.startswith("COPY ")
        for source in line.split()[1:-1]
    ]
    for required in (
        "uv.lock",
        "pyproject.toml",
        "docker/pip-bootstrap.txt",
        "docker/uv-bootstrap.txt",
        "docker/build-backend.txt",
    ):
        assert required in sources


def test_runtime_dependencies_install_from_the_lock_with_hashes() -> None:
    """uv exports the lock; pip installs it and the build backend with hashes, then the project."""
    steps = [step.strip() for step in _builder_install().removeprefix("RUN ").split("&&")]
    assert steps == [
        "pip install --no-cache-dir --require-hashes --only-binary=:all: -r /tmp/pip-bootstrap.txt",
        "pip install --no-cache-dir --require-hashes --only-binary=:all: --target /tmp/uv"
        " -r /tmp/uv-bootstrap.txt",
        "/tmp/uv/bin/uv export --locked --no-dev --no-emit-project --format requirements.txt"
        " -o /tmp/requirements.txt",
        "pip install --no-cache-dir --require-hashes --only-binary=:all: -r /tmp/requirements.txt",
        "pip install --no-cache-dir --require-hashes --only-binary=:all:"
        " --target /tmp/build-backend -r /tmp/build-backend.txt",
        "PYTHONPATH=/tmp/build-backend pip install --no-cache-dir --no-deps --no-build-isolation .",
    ]
    pip_runs = [line for line in _stages()["builder"] if line.startswith("RUN ") and "pip" in line]
    assert pip_runs == [_builder_install()]


def test_runtime_stage_copies_only_the_venv() -> None:
    """uv and the build backend stay under /tmp in the builder; the runtime gets only the venv."""
    copies = [line for line in _stages()["runtime"] if line.startswith("COPY ")]
    assert copies == ["COPY --from=builder /opt/venv /opt/venv"]


def test_build_backend_stays_out_of_the_venv() -> None:
    """PYTHONPATH reaches the build backend only for the project install, never as an ENV."""
    builder = _stages()["builder"]
    assert not any(line.startswith("ENV ") and "PYTHONPATH" in line for line in builder)
    assert "--target /tmp/build-backend -r /tmp/build-backend.txt" in _builder_install()


def test_uv_bootstrap_is_hash_pinned_and_meets_required_version() -> None:
    pins = re.findall(
        r"^uv==(\S+)((?:\s*\\\n\s+--hash=sha256:[0-9a-f]{64})+)$", UV_BOOTSTRAP, re.MULTILINE
    )
    assert len(pins) == 1
    version, hash_lines = pins[0]
    hashes = re.findall(r"sha256:([0-9a-f]{64})", hash_lines)
    assert hashes
    assert len(set(hashes)) == len(hashes)
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    required = pyproject["tool"]["uv"]["required-version"]
    assert required.startswith(">=")

    def parse(text: str) -> tuple[int, ...]:
        return tuple(int(part) for part in text.split("."))

    assert parse(version) >= parse(required.removeprefix(">="))


def _build_backend_pins() -> dict[str, tuple[str, list[str]]]:
    """Map package name to (exact version, sha256 digests) from docker/build-backend.txt.

    Any line that is not an exact, hash-pinned requirement, or a repeated name, fails.
    """
    return gen_build_backend.parse(BUILD_BACKEND)


def test_build_backend_is_hash_pinned() -> None:
    """Every build-backend requirement carries at least one sha256; names are unique."""
    (install,) = [
        line for line in _stages()["builder"] if "build-backend.txt" in line and "RUN" in line
    ]
    assert "--require-hashes" in install
    assert "--only-binary=:all:" in install
    assert "--no-build-isolation" in _builder_install()
    pins = _build_backend_pins()
    assert set(pins) >= {
        "hatchling",
        "uv-dynamic-versioning",
        "packaging",
        "pathspec",
        "pluggy",
        "tomlkit",
        "trove-classifiers",
        "dunamai",
        "jinja2",
        "markupsafe",
    }
    for name, (version, digests) in pins.items():
        assert version
        assert digests, name
        assert len(set(digests)) == len(digests), name


def test_build_backend_pins_stay_inside_build_system_ranges() -> None:
    """hatchling and uv-dynamic-versioning pins satisfy pyproject [build-system] requires."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    requires = [Requirement(item) for item in pyproject["build-system"]["requires"]]
    by_name = {req.name: req for req in requires}
    pins = _build_backend_pins()
    for name in ("hatchling", "uv-dynamic-versioning"):
        assert name in by_name, name
        assert name in pins, name
        version, _digests = pins[name]
        assert Version(version) in by_name[name].specifier, (
            name,
            version,
            by_name[name],
        )
