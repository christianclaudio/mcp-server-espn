"""Dependabot auto-merge gate: every PR commit is checked before the merge step.

Text checks only; no YAML dependency. The harness runs the script offline.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = ROOT / ".github/workflows/dependabot-automerge.yml"

_BOT = "dependabot[bot]"
_FLOW = "web-flow"
_HUMAN = "human"


def _text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def _code_lines(text: str) -> str:
    """The workflow without its full-line comments."""
    lines = text.splitlines()
    kept = [line for line in lines if not line.lstrip().startswith("#")]
    return "\n".join(kept)


def _merge_step_if(text: str) -> str:
    marker = "- name: Merge semver-minor and semver-patch updates "
    marker += "after CI succeeds"
    start = text.index(marker)
    block = text[start:]
    if_start = block.index("if: >-")
    return block[if_start : block.index("run: |", if_start)]


def test_merge_step_requires_commit_gate() -> None:
    assert "steps.commits.outputs.allowed == 'true'" in _merge_step_if(_text())


def test_merge_pins_head_commit() -> None:
    code = _code_lines(_text())
    assert 'gh pr merge --squash --match-head-commit "$HEAD_SHA"' in code
    assert "--auto" not in code


def test_classifier_rules_present() -> None:
    code = _code_lines(_text())
    for token in (
        "dependabot[bot]",
        "web-flow",
        "merge-base --is-ancestor",
        "merge-tree --write-tree",
        "--paginate",
    ):
        assert token in code, token


def test_head_sha_must_be_listed() -> None:
    """The head must be in the commit list; order is not promised."""
    assert 'cut -f1 | grep -xF "$HEAD_SHA"' in _code_lines(_text())


def test_checkout_is_pinned_and_credential_free() -> None:
    text = _text()
    assert re.search(r"actions/checkout@[0-9a-f]{40}", text)
    assert "persist-credentials: false" in text


def test_no_review_wait_or_trailer_rewrite() -> None:
    text = _text().lower()
    for token in ("coderabbit", "reviews", "co-authored-by", "--body"):
        assert token not in text, token


def test_top_level_permissions_empty() -> None:
    assert re.search(r"^permissions: \{\}$", _text(), re.MULTILINE)


def _classify_script(text: str) -> str:
    """Dedented shell of the Classify PR commits step."""
    start = text.index("- name: Classify PR commits")
    rest = text[start:]
    run_at = rest.index("run: |")
    line_start = rest.rfind("\n", 0, run_at) + 1
    run_indent = run_at - line_start
    body = rest[rest.index("\n", run_at) + 1 :]
    kept: list[str] = []
    for line in body.splitlines():
        if not line.strip():
            kept.append("")
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent <= run_indent:
            break
        kept.append(line)
    script = textwrap.dedent("\n".join(kept)).strip() + "\n"
    if script.startswith("set -euo pipefail\n"):
        return script
    message = "Classify PR commits run block was not extracted"
    raise RuntimeError(message)


def _require_tools() -> None:
    """Skip only when bash or git is missing, or git is older than 2.38."""
    if shutil.which("bash") is None:
        pytest.skip("bash is not installed")
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not installed")
    proc = subprocess.run(
        [git, "--version"],
        check=False,
        capture_output=True,
        text=True,
    )
    match = re.search(r"(\d+)\.(\d+)", proc.stdout)
    if match is None:
        return
    major = int(match.group(1))
    minor = int(match.group(2))
    if (major, minor) < (2, 38):
        pytest.skip(f"git {major}.{minor} is older than 2.38")


def _git_env(root: Path) -> dict[str, str]:
    dropped = {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"}
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in dropped and not key.startswith("GIT_CONFIG_")
    }
    env["GIT_CONFIG_GLOBAL"] = str(root / "no-global-gitconfig")
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_AUTHOR_NAME"] = "Harness"
    env["GIT_AUTHOR_EMAIL"] = "harness@example.com"
    env["GIT_COMMITTER_NAME"] = "Harness"
    env["GIT_COMMITTER_EMAIL"] = "harness@example.com"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _git(repo: Path, env: dict[str, str], *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def _init_repo(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    _require_tools()
    repo = tmp_path / "repo"
    repo.mkdir()
    env = _git_env(tmp_path)
    _git(repo, env, "init", "-q", "-b", "main")
    (repo / "README").write_text("base\n", encoding="utf-8")
    _git(repo, env, "add", "--", "README")
    _git(repo, env, "commit", "-q", "-m", "base")
    base = _git(repo, env, "rev-parse", "HEAD")
    _git(repo, env, "update-ref", "refs/remotes/origin/main", base)
    return repo, env


def _commit_file(
    repo: Path,
    env: dict[str, str],
    name: str,
    body: str,
    message: str,
) -> str:
    path = repo / name
    path.write_text(body, encoding="utf-8")
    _git(repo, env, "add", "--", name)
    _git(repo, env, "commit", "-q", "-m", message)
    return _git(repo, env, "rev-parse", "HEAD")


def _parents(repo: Path, env: dict[str, str], sha: str) -> str:
    return _git(repo, env, "log", "-1", "--format=%P", sha)


def _row(
    sha: str,
    author: str,
    committer: str,
    verified: str,
    parents: str,
) -> str:
    """Canned gh --jq row: sha, author, committer, verified, parents."""
    return "\t".join((sha, author, committer, verified, parents))


def _write_gh(directory: Path) -> None:
    """Stub gh: ignore arguments and print the canned TSV."""
    directory.mkdir()
    stub = directory / "gh"
    stub.write_text(
        '#!/bin/sh\ncat "$HARNESS_COMMITS_TSV"\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)


def _run(
    repo: Path,
    env: dict[str, str],
    rows: list[str],
    head: str,
) -> tuple[int, str, str]:
    root = repo.parent
    tsv = root / "commits.tsv"
    payload = "".join(f"{row}\n" for row in rows)
    tsv.write_text(payload, encoding="utf-8")
    bin_dir = root / "bin"
    _write_gh(bin_dir)
    output = root / "github_output.txt"
    output.write_text("", encoding="utf-8")
    script_path = root / "classify.sh"
    script_path.write_text(_classify_script(_text()), encoding="utf-8")
    run_env = dict(env)
    run_env["REPO"] = "example/repo"
    run_env["PR"] = "1"
    run_env["HEAD_SHA"] = head
    run_env["BASE_REF"] = "main"
    run_env["GITHUB_OUTPUT"] = str(output)
    run_env["GH_TOKEN"] = "dummy"
    run_env["HARNESS_COMMITS_TSV"] = str(tsv)
    previous = run_env.get("PATH", "")
    run_env["PATH"] = os.pathsep.join((str(bin_dir), previous))
    proc = subprocess.run(
        ["bash", str(script_path)],
        cwd=repo,
        env=run_env,
        check=False,
        capture_output=True,
        text=True,
    )
    allowed = ""
    text = output.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("allowed="):
            allowed = line.split("=", 1)[1]
    detail = f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    return proc.returncode, allowed, detail


def _assert_gate(
    repo: Path,
    env: dict[str, str],
    rows: list[str],
    head: str,
    expected: str,
) -> None:
    code, allowed, detail = _run(repo, env, rows, head)
    assert code == 0, detail
    assert allowed == expected, detail


def test_appended_human_commit_is_refused(tmp_path: Path) -> None:
    """A Dependabot commit followed by a human commit fails closed."""
    repo, env = _init_repo(tmp_path)
    bot = _commit_file(repo, env, "dep.txt", "1\n", "bump")
    human = _commit_file(repo, env, "note.txt", "hi\n", "note")
    rows = [
        _row(bot, _BOT, _FLOW, "true", _parents(repo, env, bot)),
        _row(human, _HUMAN, _HUMAN, "true", _parents(repo, env, human)),
    ]
    _assert_gate(repo, env, rows, human, "false")


def test_unverified_bot_commit_is_refused(tmp_path: Path) -> None:
    """An unverified dependabot[bot] commit by web-flow fails closed."""
    repo, env = _init_repo(tmp_path)
    bot = _commit_file(repo, env, "dep.txt", "1\n", "bump")
    rows = [_row(bot, _BOT, _FLOW, "false", _parents(repo, env, bot))]
    _assert_gate(repo, env, rows, bot, "false")


def test_single_verified_dependabot_commit_is_allowed(tmp_path: Path) -> None:
    """One verified Dependabot commit is allowed; the harness is not vacuous."""
    repo, env = _init_repo(tmp_path)
    bot = _commit_file(repo, env, "dep.txt", "1\n", "bump")
    rows = [_row(bot, _BOT, _FLOW, "true", _parents(repo, env, bot))]
    _assert_gate(repo, env, rows, bot, "true")


def test_clean_update_branch_merge_is_allowed(tmp_path: Path) -> None:
    """A verified web-flow merge of origin/main on a bot commit is allowed."""
    repo, env = _init_repo(tmp_path)
    bot = _commit_file(repo, env, "dep.txt", "1\n", "bump")
    base = _git(repo, env, "rev-parse", "refs/remotes/origin/main")
    _git(repo, env, "checkout", "-q", "--detach", base)
    moved = _commit_file(repo, env, "main.txt", "2\n", "move main")
    _git(repo, env, "update-ref", "refs/remotes/origin/main", moved)
    _git(repo, env, "checkout", "-q", "--detach", bot)
    _git(repo, env, "merge", "--no-ff", "-m", "merge main", moved)
    merged = _git(repo, env, "rev-parse", "HEAD")
    rows = [
        _row(bot, _BOT, _FLOW, "true", _parents(repo, env, bot)),
        _row(merged, _BOT, _FLOW, "true", _parents(repo, env, merged)),
    ]
    assert _parents(repo, env, merged) == f"{bot} {moved}"
    _assert_gate(repo, env, rows, merged, "true")


def test_head_missing_from_commit_list_is_refused(tmp_path: Path) -> None:
    """A head SHA that is not in the commit list fails closed."""
    repo, env = _init_repo(tmp_path)
    bot = _commit_file(repo, env, "dep.txt", "1\n", "bump")
    base = _git(repo, env, "rev-parse", "refs/remotes/origin/main")
    rows = [_row(bot, _BOT, _FLOW, "true", _parents(repo, env, bot))]
    _assert_gate(repo, env, rows, base, "false")
