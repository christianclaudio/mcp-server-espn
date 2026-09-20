"""Calculate SemVer release increment based on conventional commits since the latest tag."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple


class BumpRecommendation(NamedTuple):
    """Container for bump calculation results."""

    bump_type: str  # "MAJOR", "MINOR", "PATCH", or "NONE"
    current_version: str
    suggested_version: str
    commit_count: int
    breaking_commits: list[str]
    feat_commits: list[str]
    fix_commits: list[str]
    other_commits: list[str]


def get_current_version(repo_root: Path) -> str:
    """Extract current project version from pyproject.toml."""
    pyproject = repo_root / "pyproject.toml"
    if not pyproject.exists():
        return "0.0.0"
    content = pyproject.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
    return match.group(1) if match else "0.0.0"


def get_latest_tag() -> str | None:
    """Retrieve latest git tag, or None if no tags exist."""
    try:
        res = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            capture_output=True,
            text=True,
            check=True,
        )
        return res.stdout.strip() or None
    except subprocess.CalledProcessError:
        return None


def get_commits_since_tag(tag: str | None) -> list[str]:
    """Retrieve commit messages (including bodies) since the specified tag or from repo start."""
    range_spec = f"{tag}..HEAD" if tag else "HEAD"
    try:
        res = subprocess.run(
            ["git", "log", range_spec, "--pretty=format:%B%x1e"],
            capture_output=True,
            text=True,
            check=True,
        )
        lines = [line.strip() for line in res.stdout.split("\x1e") if line.strip()]
        return lines
    except subprocess.CalledProcessError:
        return []


def increment_semver(version: str, bump_type: str) -> str:
    """Calculate next SemVer version string."""
    match = re.match(r"^v?(\d+)\.(\d+)\.(\d+)(.*)$", version)
    if not match:
        return version
    major, minor, patch = int(match.group(1)), int(match.group(2)), int(match.group(3))
    suffix = match.group(4)

    if bump_type == "MAJOR":
        return f"{major + 1}.0.0{suffix}"
    if bump_type == "MINOR":
        return f"{major}.{minor + 1}.0{suffix}"
    if bump_type == "PATCH":
        return f"{major}.{minor}.{patch + 1}{suffix}"
    return version


def analyze_commits(commits: list[str], current_version: str) -> BumpRecommendation:
    """Analyze commit messages and determine the required SemVer bump."""
    breaking: list[str] = []
    features: list[str] = []
    fixes: list[str] = []
    others: list[str] = []

    breaking_pattern = re.compile(r"^[a-zA-Z]+(\([^\)]+\))?!:|BREAKING CHANGE:", re.IGNORECASE)
    feat_pattern = re.compile(r"^feat(\([^\)]+\))?:", re.IGNORECASE)
    fix_pattern = re.compile(r"^(fix|perf)(\([^\)]+\))?:", re.IGNORECASE)

    for commit in commits:
        if breaking_pattern.search(commit):
            breaking.append(commit)
        elif feat_pattern.search(commit):
            features.append(commit)
        elif fix_pattern.search(commit):
            fixes.append(commit)
        else:
            others.append(commit)

    if breaking:
        bump = "MAJOR"
    elif features:
        bump = "MINOR"
    elif fixes:
        bump = "PATCH"
    else:
        bump = "NONE"

    suggested = increment_semver(current_version, bump)

    return BumpRecommendation(
        bump_type=bump,
        current_version=current_version,
        suggested_version=suggested,
        commit_count=len(commits),
        breaking_commits=breaking,
        feat_commits=features,
        fix_commits=fixes,
        other_commits=others,
    )


def main() -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Calculate SemVer release increment.")
    parser.add_argument("--json", action="store_true", help="Output results as JSON.")
    args = parser.parse_args()

    repo_root = Path.cwd()
    current_version = get_current_version(repo_root)
    latest_tag = get_latest_tag()
    commits = get_commits_since_tag(latest_tag)
    rec = analyze_commits(commits, current_version)

    if args.json:
        payload = {
            "bump_type": rec.bump_type,
            "current_version": rec.current_version,
            "suggested_version": rec.suggested_version,
            "latest_tag": latest_tag,
            "commit_count": rec.commit_count,
            "breaking": rec.breaking_commits,
            "features": rec.feat_commits,
            "fixes": rec.fix_commits,
            "others": rec.other_commits,
        }
        print(json.dumps(payload, indent=2))
        return 0

    print("=== SemVer Release Bump Recommendation ===")
    print(f"Latest tag:       {latest_tag or 'None (initial release)'}")
    print(f"Current version:  {rec.current_version}")
    print(f"Total commits:    {rec.commit_count}")
    print(f"Recommended bump: {rec.bump_type}")
    print(f"Suggested version:{rec.suggested_version}")
    print("------------------------------------------")
    if rec.breaking_commits:
        print(f"Breaking changes ({len(rec.breaking_commits)}):")
        for c in rec.breaking_commits:
            print(f"  ! {c}")
    if rec.feat_commits:
        print(f"Features ({len(rec.feat_commits)}):")
        for c in rec.feat_commits:
            print(f"  + {c}")
    if rec.fix_commits:
        print(f"Fixes & perf ({len(rec.fix_commits)}):")
        for c in rec.fix_commits:
            print(f"  * {c}")
    if rec.other_commits:
        print(f"Maintenance/other ({len(rec.other_commits)}):")
        for c in rec.other_commits:
            print(f"  . {c}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
