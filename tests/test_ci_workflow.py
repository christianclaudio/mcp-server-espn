"""Canonical CI job ids, check names, and required-status contexts.

Check names carry no tool counts, spec dates, or versions. The test matrix job
is the only exception: its name may contain a digit. Text checks only; no YAML
dependency.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CI_PATH = ROOT / ".github/workflows/ci.yml"
GOVERNANCE_PATH = ROOT / "scripts/setup_repo_governance.sh"
CONFORMANCE_PATH = ROOT / "scripts/check_conformance.sh"

# Job id at two-space indent, then the job's own ``name:`` (four spaces, not a step).
_JOB_NAME = re.compile(r"^  ([a-z0-9-]+):\n(?:    .+\n)*?    name: (.+)$", re.MULTILINE)

EXPECTED_JOBS = {
    "lint-and-types": "Lint and types",
    "test": "Tests (py${{ matrix.python-version }})",
    "tests": "Tests",
    "tool-contract": "Tool contract assertions",
    "conformance": "Protocol conformance",
    "build-check": "Build and package check",
    "docker-build": "Container image build",
    "drift-check": "Check API Drift",
}

EXPECTED_CONTEXTS = [
    "Lint and types",
    "Tests",
    "Tool contract assertions",
    "Protocol conformance",
    "Build and package check",
    "Container image build",
]


def _ci_text() -> str:
    return CI_PATH.read_text(encoding="utf-8")


def _jobs_section(text: str) -> str:
    marker = "\njobs:\n"
    index = text.find(marker)
    assert index != -1, "ci.yml has no jobs: block"
    return text[index + len(marker) :]


def _job_names(text: str) -> dict[str, str]:
    return {match.group(1): match.group(2).strip() for match in _JOB_NAME.finditer(text)}


def _job_block(text: str, job_id: str) -> str:
    match = re.search(
        rf"^  {re.escape(job_id)}:\n(.*?)(?=^  [a-z0-9-]+:\n|\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None, job_id
    return match.group(1)


def test_job_ids_and_names() -> None:
    """The workflow's job ids and check names are the canonical set."""
    jobs = _job_names(_jobs_section(_ci_text()))
    assert jobs == EXPECTED_JOBS


def test_job_names_have_no_digits_except_the_test_matrix() -> None:
    """A check name embeds no count, date, or version. The matrix job may."""
    for job_id, name in _job_names(_jobs_section(_ci_text())).items():
        if job_id == "test":
            continue
        assert re.search(r"\d", name) is None, f"{job_id} name {name!r} contains a digit"


def test_aggregate_tests_job_fails_unless_the_matrix_succeeds() -> None:
    """``tests`` is the required context: it needs ``test``, always runs, and
    fails unless ``needs.test.result`` is ``success``.
    """
    body = _job_block(_jobs_section(_ci_text()), "tests")
    assert re.search(r"^    name: Tests$", body, re.MULTILINE)
    assert re.search(r"^    needs: test$", body, re.MULTILINE)
    assert re.search(r"^    if: always\(\)$", body, re.MULTILINE)
    assert re.search(r'needs\.test\.result \}\}" != "success"', body)


def test_conformance_is_its_own_job() -> None:
    """Node and the conformance script live on ``conformance``, not ``tool-contract``.

    OpenAPI drift and the stdio protocol tests stay on ``tool-contract``.
    """
    jobs = _jobs_section(_ci_text())
    conformance = _job_block(jobs, "conformance")
    tool_contract = _job_block(jobs, "tool-contract")
    assert 'node-version: "22"' in conformance
    assert "./scripts/check_conformance.sh" in conformance
    assert "setup-node" not in tool_contract
    assert "check_conformance.sh" not in tool_contract
    assert "scripts/check_openapi_drift.py" in tool_contract
    assert "tests/test_protocol.py" in tool_contract
    script = CONFORMANCE_PATH.read_text(encoding="utf-8")
    assert 'CONFORMANCE_VERSION="${CONFORMANCE_VERSION:-0.1.16}"' in script


def test_drift_check_is_the_only_job_that_opens_issues() -> None:
    """The scheduled drift job fails on new routes and can open an issue. Nothing else can."""
    jobs = _jobs_section(_ci_text())
    drift = _job_block(jobs, "drift-check")
    assert "--fail-on-new-uncovered" in drift
    assert "issues: write" in drift
    assert "contents: read" in drift
    assert "Create Issue on Failure" in drift
    assert "actions/github-script@3a2844b7e9c422d3c10d287c895573f7108da1b3" in drift
    assert "espn drift monitor failed" in drift
    assert "New uncovered" in drift
    assert "Dead site route" in drift
    for job_id in EXPECTED_JOBS:
        if job_id == "drift-check":
            continue
        body = _job_block(jobs, job_id)
        assert "--fail-on-new-uncovered" not in body
        assert "issues: write" not in body


def test_governance_required_contexts() -> None:
    """Required checks use the aggregate Tests name plus image build and conformance."""
    text = GOVERNANCE_PATH.read_text(encoding="utf-8")
    match = re.search(
        r'"required_status_checks": \{\s*"strict": true,\s*"contexts": \[(.*?)\]',
        text,
        re.DOTALL,
    )
    assert match is not None
    contexts = re.findall(r'"([^"]+)"', match.group(1))
    assert contexts == EXPECTED_CONTEXTS
    assert "Tests (py" not in text
    for name in contexts:
        assert re.search(r"\d", name) is None, name
