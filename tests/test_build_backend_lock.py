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
    with pytest.raises(ValueError, match="one python"):
        gen_build_backend.base_python("FROM python:3.13-slim\nFROM python:3.14-slim\n")


# The digest pinned on both FROM lines. glibc comes from the trixie suffix, not this digest.
_PINNED_DIGEST = "sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b"


def test_image_glibc_is_pinned_to_the_dockerfile_base() -> None:
    dockerfile = (gen_build_backend.ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert dockerfile.count(f"FROM python:3.12-slim-trixie@{_PINNED_DIGEST}") == 2
    assert gen_build_backend.base_python(dockerfile) == "3.12"
    assert gen_build_backend.image_glibc(dockerfile) == (2, 41)


def test_image_glibc_accepts_a_digest_change_within_trixie() -> None:
    other = "sha256:" + "ab" * 32
    text = f"FROM python:3.14-slim-trixie@{other} AS builder\n"
    assert gen_build_backend.image_glibc(text) == (2, 41)


def test_image_glibc_reads_a_bookworm_base() -> None:
    digest = "sha256:" + "cd" * 32
    text = f"FROM python:3.14-slim-bookworm@{digest}\n"
    assert gen_build_backend.image_glibc(text) == (2, 36)


def test_image_glibc_rejects_an_unpinned_base() -> None:
    with pytest.raises(ValueError, match="pinned"):
        gen_build_backend.image_glibc("FROM python:3.14-slim-trixie\n")


def test_image_glibc_rejects_a_tag_with_no_release() -> None:
    digest = "sha256:" + "ab" * 32
    with pytest.raises(ValueError, match="no Debian release"):
        gen_build_backend.image_glibc(f"FROM python:3.14-slim@{digest}\n")


def test_image_glibc_rejects_two_releases() -> None:
    digest = "sha256:" + "ab" * 32
    text = f"FROM python:3.14-slim-trixie@{digest}\nFROM python:3.14-slim-bookworm@{digest}\n"
    with pytest.raises(ValueError, match="one Debian release"):
        gen_build_backend.image_glibc(text)


def test_image_glibc_rejects_an_unknown_release() -> None:
    digest = "sha256:" + "ef" * 32
    with pytest.raises(ValueError, match="no glibc entry"):
        gen_build_backend.image_glibc(f"FROM python:3.14-slim-forky@{digest}\n")


def test_image_glibc_rejects_mixed_python_versions() -> None:
    text = "FROM python:3.13-slim-trixie\nFROM python:3.14-slim-trixie\n"
    with pytest.raises(ValueError, match="one python"):
        gen_build_backend.image_glibc(text)


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


def test_digests_skips_a_floor_above_the_image_and_keeps_at_or_below() -> None:
    at_floor = "1" * 64
    above = "2" * 64
    below = "3" * 64
    package = {
        "name": "markupsafe",
        "wheels": [
            _wheel("markupsafe-3.0.4-cp314-cp314-manylinux_2_29_x86_64.whl", above),
            _wheel("markupsafe-3.0.4-cp314-cp314-manylinux_2_28_x86_64.whl", at_floor),
            _wheel("markupsafe-3.0.4-cp314-cp314-manylinux_2_17_aarch64.whl", below),
        ],
    }
    assert gen_build_backend._digests(package, "3.14", (2, 28)) == [at_floor, below]


def test_digests_keeps_a_multi_tag_wheel_when_any_manylinux_floor_fits() -> None:
    x86_64 = "a" * 64
    aarch64 = "b" * 64
    package = {
        "name": "markupsafe",
        "wheels": [
            _wheel(
                "markupsafe-3.0.4-cp314-cp314-manylinux_2_35_x86_64.manylinux_2_28_x86_64.whl",
                x86_64,
            ),
            _wheel(
                "markupsafe-3.0.4-cp314-cp314-manylinux_2_35_aarch64.manylinux2014_aarch64.whl",
                aarch64,
            ),
        ],
    }
    assert gen_build_backend._digests(package, "3.14", (2, 28)) == [x86_64, aarch64]


@pytest.mark.parametrize("alias", ["manylinux1", "manylinux2010", "manylinux2014"])
def test_digests_keeps_legacy_manylinux_aliases(alias: str) -> None:
    x86_64 = "1" * 64
    aarch64 = "2" * 64
    package = {
        "name": "markupsafe",
        "wheels": [
            _wheel(f"markupsafe-3.0.4-cp314-cp314-{alias}_x86_64.whl", x86_64),
            _wheel(f"markupsafe-3.0.4-cp314-cp314-{alias}_aarch64.whl", aarch64),
        ],
    }
    # manylinux2014 is glibc 2.17, so this floor is exactly that alias and above the older two.
    assert gen_build_backend._digests(package, "3.14", (2, 17)) == [x86_64, aarch64]


def test_digests_never_counts_musllinux() -> None:
    package = {
        "name": "markupsafe",
        "wheels": [
            _wheel("markupsafe-3.0.4-cp314-cp314-musllinux_1_2_x86_64.whl", "1" * 64),
            _wheel("markupsafe-3.0.4-cp314-cp314-musllinux_1_2_aarch64.whl", "2" * 64),
        ],
    }
    with pytest.raises(ValueError, match="no single cp314 wheel per arch"):
        gen_build_backend._digests(package, "3.14", (2, 41))
