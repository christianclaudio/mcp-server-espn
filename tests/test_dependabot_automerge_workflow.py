"""Dependabot auto-merge gate: every PR commit is checked before the merge step.

Text checks only; no YAML dependency.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = ROOT / ".github/workflows/dependabot-automerge.yml"


def _text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def _code_lines(text: str) -> str:
    """The workflow without its full-line comments."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def _merge_step_if(text: str) -> str:
    start = text.index("- name: Merge semver-minor and semver-patch updates after CI succeeds")
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
    """The head must be somewhere in the commit list; GitHub does not promise the order."""
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
