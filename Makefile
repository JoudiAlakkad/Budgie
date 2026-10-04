# Local entry points for the checks in docs/wiki/decisions/0010-ci-gates-before-merge.md.
# CI calls the same targets.

VENV   ?= .venv
PY     := $(VENV)/bin/python
CONFIG := backend/pyproject.toml

.PHONY: install lint format test imports openapi openapi-check secrets check run docker-check

install:
	python3 -m venv $(VENV)
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e "./backend[dev]"

lint:
	$(VENV)/bin/ruff check --config $(CONFIG) backend tests scripts
	$(VENV)/bin/ruff format --check --config $(CONFIG) backend tests scripts

format:
	$(VENV)/bin/ruff check --fix --config $(CONFIG) backend tests scripts
	$(VENV)/bin/ruff format --config $(CONFIG) backend tests scripts

test:
	$(PY) -m pytest -c $(CONFIG) --rootdir . -m "not integration" --cov=app --cov-report=term-missing tests

imports:
	$(VENV)/bin/lint-imports --config $(CONFIG)

openapi:
	$(PY) -m app.openapi_export --out docs/openapi.json

openapi-check:
	$(PY) -m app.openapi_export --out docs/openapi.json --check

# gitleaks is not installed in the dev container; CI always runs it.
secrets:
	@if command -v gitleaks >/dev/null 2>&1; then gitleaks detect --no-banner; \
	else echo "gitleaks not installed, skipped (runs in CI)"; fi

check: lint test imports openapi-check secrets

run:
	$(VENV)/bin/uvicorn app.main:app --reload --port 8000

# Needs docker, so run it on the host (the dev container has none).
docker-check:
	scripts/docker-health-check.sh
