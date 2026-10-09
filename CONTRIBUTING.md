# Contributing Guidelines

Thank you for your interest in contributing!

1. Fork or branch from `main`: `git checkout -b feat/your-feature`.
2. Ensure strict static typing: `mypy --strict src/`.
3. Ensure the test suite passes: `pytest` (coverage threshold configured in `pyproject.toml`).
4. Validate tool contracts: `python scripts/check_tool_contract.py`.
5. Open a Pull Request. Merges are performed via **Squash Merge** with Conventional Commit titles (`feat:`, `fix:`, `docs:`, `chore:`).
6. Do not edit version numbers or `CHANGELOG.md`: the git tag is the version, and GitHub Releases are the changelog. The squash commit message is the PR body, so a breaking (`feat!:` / `fix!:`) PR ends its body with a `BREAKING CHANGE:` footer that includes the migration steps.
