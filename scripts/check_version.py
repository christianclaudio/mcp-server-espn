#!/usr/bin/env python3
"""Fail when the built wheel reports a version no real build should have.

Usage::

    uv build
    python scripts/check_version.py [--dist-dir DIR] [--allow-untagged]

Run it after ``uv build``. It reads the version from the wheel that ships, the same
way release.yml's tag check does: ``DIR`` (default ``dist``) must hold exactly one
``*.whl``, and the version is the first ``Version:`` line of its
``*.dist-info/METADATA``. It never asks the installed package, because an editable
install can report a stale version.

The version comes from the git tag (uv-dynamic-versioning). Two versions are rejected:

* ``0.0.0``: the ``fallback-version``, reported by a build without git metadata
  (no ``.git``, for example a Docker context or a source copy).
* ``0.0.1.devN[+sha]``: reported when no ``v*`` tag is reachable, which in CI means
  a shallow checkout (set ``fetch-depth: 0``).

A normal untagged build such as ``1.3.1.dev2+g22ddd38`` passes, so this runs on PR
and main builds. It does not check that the build is at a tag; release.yml compares
the wheel's version to the tag itself.

``--allow-untagged`` accepts ``0.0.1.devN``. Use it only in a fresh copy of the
template until that copy has its first tag.
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import sys
import zipfile
from collections.abc import Sequence
from pathlib import Path

DEFAULT_DIST_DIR = "dist"
METADATA_GLOB = "*.dist-info/METADATA"
VERSION_PREFIX = "Version: "
FALLBACK_VERSION = "0.0.0"
UNTAGGED = re.compile(r"^0\.0\.1\.dev\d+(?:\+.*)?$")


class WheelError(RuntimeError):
    """dist/ does not hold exactly one readable wheel with a version."""


def find_wheel(dist_dir: Path) -> Path:
    """Return the only ``*.whl`` in ``dist_dir``, or raise ``WheelError``."""
    wheels = sorted(dist_dir.glob("*.whl"))
    if len(wheels) != 1:
        raise WheelError(
            f"expected exactly one wheel in {dist_dir}/, found {len(wheels)} "
            "(run uv build first, from a clean dist/)"
        )
    return wheels[0]


def wheel_version(wheel: Path) -> str:
    """Return the first ``Version:`` value in the wheel's ``*.dist-info/METADATA``.

    Mirrors release.yml: ``unzip -p WHEEL '*.dist-info/METADATA' | sed -n
    's/^Version: //p' | head -n 1``.
    """
    try:
        with zipfile.ZipFile(wheel) as archive:
            members = [n for n in archive.namelist() if fnmatch.fnmatchcase(n, METADATA_GLOB)]
            if not members:
                raise WheelError(f"{wheel.name} has no {METADATA_GLOB}")
            text = "".join(archive.read(name).decode("utf-8", errors="replace") for name in members)
    except zipfile.BadZipFile:
        raise WheelError(f"{wheel.name} is not a valid wheel (zip) file") from None
    for line in text.split("\n"):
        if line.startswith(VERSION_PREFIX):
            return line[len(VERSION_PREFIX) :]
    raise WheelError(f"{wheel.name} METADATA has no Version: line")


def problem(found: str, *, allow_untagged: bool = False) -> str | None:
    """Return why ``found`` is not an acceptable build version, or None if it is."""
    if found == FALLBACK_VERSION:
        return (
            f"version is the fallback {FALLBACK_VERSION}: the build had no git metadata. "
            "Check out with fetch-depth: 0, or set UV_DYNAMIC_VERSIONING_BYPASS for "
            "builds without .git (Docker)."
        )
    if not allow_untagged and UNTAGGED.match(found):
        return (
            f"version {found} means no v* tag is reachable (shallow checkout?). "
            "Check out with fetch-depth: 0."
        )
    return None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dist-dir",
        default=DEFAULT_DIST_DIR,
        help=f"directory holding the built wheel ({DEFAULT_DIST_DIR})",
    )
    parser.add_argument(
        "--allow-untagged",
        action="store_true",
        help="accept 0.0.1.devN (no v* tag reachable); only before a copy's first tag",
    )
    args = parser.parse_args(argv)
    try:
        wheel = find_wheel(Path(args.dist_dir))
        found = wheel_version(wheel)
    except WheelError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    reason = problem(found, allow_untagged=args.allow_untagged)
    if reason is not None:
        print(f"error: {wheel.name} {reason}", file=sys.stderr)
        return 1
    print(f"{wheel.name}: version {found}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
