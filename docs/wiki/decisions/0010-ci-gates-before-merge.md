# 0010 — CI gates before merge

**Status:** Accepted (2026-09-28). Implemented in F1.

## Context
Much of the code is written by agents. Every change needs the same objective checks before it reaches `main`.

## Decision
`.github/workflows/ci.yml` runs on every PR and on every push to `main`:

1. `ruff check` and `ruff format --check`
2. `pytest -m "not integration"`, using the fake LLM client, with coverage
3. OpenAPI drift: the spec generated from the app must equal `docs/openapi.json`
4. `docker build`, then start the container and check that `/api/health` returns 200 with `llm: down`
5. `lint-imports`, which enforces the layer boundaries from [0006](0006-sqlite-behind-repository-layer.md)
6. `gitleaks`, a secret scan

- `main` is protected: changes go through PRs, and all checks must pass.
- The same checks run locally with `make check` and `pre-commit`.

## Consequences
- Agents must run `make check` before they report a feature as done.
