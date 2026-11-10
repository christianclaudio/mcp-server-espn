"""docker/build-backend.txt matches the build-backend dependency group in uv.lock.

The group in pyproject.toml holds exact hatchling and uv-dynamic-versioning pins, uv.lock carries
their closure with hashes, and scripts/gen_build_backend.py writes the Docker requirements file
from the lock. These tests fail if the file drifts from the lock or the pins leave the
[build-system] ranges.
"""

from __future__ import annotations

import tomllib
from typing import Any

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

from scripts import gen_build_backend

HASH = "0" * 64


def test_build_backend_file_matches_uv_lock() -> None:
    assert gen_build_backend.check() == []


def test_build_backend_file_is_rendered_from_uv_lock() -> None:
    text = gen_build_backend.TARGET.read_text(encoding="utf-8")
    assert text == gen_build_backend.render(gen_build_backend.expected())


def test_group_pins_are_exact_and_inside_build_system_ranges() -> None:
    pyproject = tomllib.loads((gen_build_backend.ROOT / "pyproject.toml").read_text("utf-8"))
    group = [Requirement(item) for item in pyproject["dependency-groups"]["build-backend"]]
    required = {
        canonicalize_name(req.name): req
        for req in map(Requirement, pyproject["build-system"]["requires"])
    }
    assert {canonicalize_name(req.name) for req in group} == set(required)
    pins = gen_build_backend.expected()
    for req in group:
        (spec,) = req.specifier
        assert spec.operator == "==", req
        name = canonicalize_name(req.name)
        assert Version(spec.version) in required[name].specifier, (req, required[name])
        assert pins[name][0] == spec.version, (name, pins[name][0])


def test_base_python_reads_the_dockerfile() -> None:
    assert gen_build_backend.base_python("FROM python:3.14-slim@sha256:abc AS builder\n") == "3.14"
    suffixed = "FROM python:3.12-slim-trixie@sha256:" + "a" * 64 + " AS builder\n"
    assert gen_build_backend.base_python(suffixed) == "3.12"
    with pytest.raises(ValueError, match="one python"):
        gen_build_backend.base_python("FROM python:3.13-slim\nFROM python:3.14-slim\n")


def _pinned(release: str, python: str = "3.12") -> str:
    return f"FROM python:{python}-slim-{release}@sha256:{'a' * 64} AS builder\n"


def test_image_glibc_reads_the_debian_release() -> None:
    assert gen_build_backend.image_glibc(_pinned("trixie")) == (2, 41)
    assert gen_build_backend.image_glibc(_pinned("bookworm")) == (2, 36)
    dockerfile = (gen_build_backend.ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert gen_build_backend.image_glibc(dockerfile) == (2, 41)
    assert gen_build_backend.base_python(dockerfile) == "3.12"


def test_image_glibc_rejects_an_unpinned_base() -> None:
    with pytest.raises(ValueError, match="unpinned"):
        gen_build_backend.image_glibc("FROM python:3.12-slim-trixie AS builder\n")
    with pytest.raises(ValueError, match="unpinned"):
        gen_build_backend.image_glibc("")


def test_image_glibc_rejects_a_missing_release_suffix() -> None:
    text = "FROM python:3.12-slim@sha256:" + "a" * 64 + " AS builder\n"
    with pytest.raises(ValueError, match="release suffix"):
        gen_build_backend.image_glibc(text)


def test_image_glibc_rejects_mixed_releases() -> None:
    text = _pinned("trixie") + _pinned("bookworm").replace("AS builder", "AS runtime")
    with pytest.raises(ValueError, match="mixed"):
        gen_build_backend.image_glibc(text)


def test_image_glibc_rejects_an_unknown_release() -> None:
    with pytest.raises(ValueError, match="unknown"):
        gen_build_backend.image_glibc(_pinned("forky"))


@pytest.mark.parametrize(
    "text",
    [
        "hatchling==1.33.0\n",
        "hatchling>=1.33.0 --hash=sha256:" + HASH + "\n",
        "hatchling==1.33.0 --hash=sha256:abc\n",
        "-r other.txt\n",
    ],
)
def test_parse_rejects_lines_that_are_not_exact_and_hashed(text: str) -> None:
    with pytest.raises(ValueError, match="not an exact"):
        gen_build_backend.parse(text)


def test_parse_rejects_a_repeated_name() -> None:
    line = "--hash=sha256:" + HASH
    text = f"MarkupSafe==3.0.4 {line}\nmarkupsafe==3.0.4 \\\n    {line}\n"
    with pytest.raises(ValueError, match="more than once"):
        gen_build_backend.parse(text)


def test_render_round_trips_through_parse() -> None:
    pins = {"hatchling": ("1.33.0", [HASH]), "markupsafe": ("3.0.4", ["1" * 64, HASH])}
    text = gen_build_backend.render(pins)
    assert gen_build_backend.parse(text) == {
        "hatchling": ("1.33.0", [HASH]),
        "markupsafe": ("3.0.4", [HASH, "1" * 64]),
    }


def _forked_lock(first: str, second: str) -> dict[str, Any]:
    """A lock with a forked package inside the build-backend closure and one outside it."""
    root = {"name": "demo", "version": "1.0.0", "source": {"editable": "."}}
    root["dev-dependencies"] = {"build-backend": [{"name": "hatchling"}]}
    dependencies = [
        {"name": "shared", "version": "1.0", "marker": first},
        {"name": "shared", "version": "2.0", "marker": second},
    ]
    backend = {"name": "hatchling", "version": "1.33.0", "dependencies": dependencies}
    shared = [{"name": "shared", "version": version} for version in ("1.0", "2.0")]
    # Two entries for a name outside the closure, as uv writes for a marker fork.
    outside = [{"name": "beartype", "version": version} for version in ("0.22.9", "0.23.0")]
    return {"package": [root, backend, *shared, *outside]}


def test_closure_picks_the_locked_version_of_a_forked_package() -> None:
    lock = _forked_lock('python_version < "3.15"', 'python_version >= "3.15"')
    closure = gen_build_backend._closure(lock, "3.14")
    assert [(p["name"], p["version"]) for p in closure] == [
        ("hatchling", "1.33.0"),
        ("shared", "1.0"),
    ]


def test_closure_rejects_two_versions_of_one_package() -> None:
    lock = _forked_lock('platform_machine == "x86_64"', 'platform_machine == "aarch64"')
    with pytest.raises(ValueError, match="more than one version"):
        gen_build_backend._closure(lock, "3.14")


def _wheel(filename: str, digest: str) -> dict[str, str]:
    return {"url": f"https://files.example/{filename}", "hash": f"sha256:{digest}"}


def test_digests_picks_one_native_wheel_per_arch() -> None:
    x86_64 = "1" * 64
    aarch64 = "2" * 64
    package = {
        "name": "markupsafe",
        "wheels": [
            _wheel("markupsafe-3.0.4-cp313-cp313-manylinux2014_x86_64.whl", "3" * 64),
            _wheel("markupsafe-3.0.4-cp312-cp312-manylinux2014_x86_64.whl", x86_64),
            _wheel("markupsafe-3.0.4-cp312-cp312-manylinux2014_aarch64.whl", aarch64),
            _wheel("markupsafe-3.0.4-cp312-cp312-musllinux_1_2_x86_64.whl", "4" * 64),
            _wheel("markupsafe-3.0.4-cp312-cp312-win_amd64.whl", "5" * 64),
        ],
    }
    assert gen_build_backend._digests(package, "3.12", (2, 41)) == [x86_64, aarch64]


def test_digests_prefers_the_py3_none_any_wheel() -> None:
    universal = "6" * 64
    package = {"name": "pluggy", "wheels": [_wheel("pluggy-1.6.0-py3-none-any.whl", universal)]}
    assert gen_build_backend._digests(package, "3.12", (2, 41)) == [universal]


def test_digests_rejects_a_missing_arch() -> None:
    wheel = _wheel("markupsafe-3.0.4-cp312-cp312-manylinux2014_x86_64.whl", "1" * 64)
    with pytest.raises(ValueError, match="no single cp312 wheel per arch"):
        gen_build_backend._digests({"name": "markupsafe", "wheels": [wheel]}, "3.12", (2, 41))


def test_digests_ignores_musllinux() -> None:
    wheel = _wheel("markupsafe-3.0.4-cp312-cp312-musllinux_1_2_x86_64.whl", "4" * 64)
    arm = _wheel("markupsafe-3.0.4-cp312-cp312-musllinux_1_2_aarch64.whl", "5" * 64)
    with pytest.raises(ValueError, match="no single cp312 wheel per arch"):
        gen_build_backend._digests({"name": "markupsafe", "wheels": [wheel, arm]}, "3.12", (2, 41))


def test_digests_rejects_a_manylinux_floor_above_the_image_glibc() -> None:
    """manylinux_2_39 needs glibc 2.39; bookworm's libc6 is 2.36, so the wheel does not count."""
    x86 = _wheel("markupsafe-3.0.4-cp312-cp312-manylinux_2_39_x86_64.whl", "1" * 64)
    arm = _wheel("markupsafe-3.0.4-cp312-cp312-manylinux2014_aarch64.whl", "2" * 64)
    package = {"name": "markupsafe", "wheels": [x86, arm]}
    with pytest.raises(ValueError, match="no single cp312 wheel per arch"):
        gen_build_backend._digests(package, "3.12", (2, 36))
    assert gen_build_backend._digests(package, "3.12", (2, 39)) == ["1" * 64, "2" * 64]


def test_digests_uses_the_highest_manylinux_floor_on_the_wheel() -> None:
    """An older alias can make pip install a wheel whose real floor is newer."""
    filename = "markupsafe-3.0.4-cp312-cp312-manylinux2014_x86_64.manylinux_2_39_x86_64.whl"
    x86 = _wheel(filename, "1" * 64)
    arm = _wheel("markupsafe-3.0.4-cp312-cp312-manylinux2014_aarch64.whl", "2" * 64)
    package = {"name": "markupsafe", "wheels": [x86, arm]}
    with pytest.raises(ValueError, match="no single cp312 wheel per arch"):
        gen_build_backend._digests(package, "3.12", (2, 36))
    assert gen_build_backend._digests(package, "3.12", (2, 39)) == ["1" * 64, "2" * 64]


@pytest.mark.parametrize(
    ("alias", "floor"),
    [
        ("manylinux1", (2, 5)),
        ("manylinux2010", (2, 12)),
        ("manylinux2014", (2, 17)),
    ],
)
def test_digests_accepts_legacy_manylinux_aliases_at_their_glibc_floor(
    alias: str, floor: tuple[int, int]
) -> None:
    x86 = _wheel(f"pkg-1.0.0-cp312-cp312-{alias}_x86_64.whl", "1" * 64)
    arm = _wheel(f"pkg-1.0.0-cp312-cp312-{alias}_aarch64.whl", "2" * 64)
    package = {"name": "pkg", "wheels": [x86, arm]}
    assert gen_build_backend._digests(package, "3.12", floor) == ["1" * 64, "2" * 64]
    below = (floor[0], floor[1] - 1)
    with pytest.raises(ValueError, match="no single cp312 wheel per arch"):
        gen_build_backend._digests(package, "3.12", below)
