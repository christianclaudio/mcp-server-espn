# Contributing Guidelines

Thank you for your interest in contributing!

1. Fork or branch from `main`: `git checkout -b feat/your-feature`.
2. Ensure strict static typing: `mypy --strict src/`.
3. Ensure 100% test coverage: `pytest --cov=src/template_mcp --cov-fail-under=100`.
4. Validate tool contracts: `python scripts/check_tool_contract.py`.
5. Open a Pull Request. Merges are performed via **Squash Merge** with Conventional Commit titles (`feat:`, `fix:`, `docs:`, `chore:`).
