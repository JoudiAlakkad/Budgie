# Local entry points for the checks in docs/wiki/decisions/0010-ci-gates-before-merge.md.
# CI calls the same targets.

VENV   ?= .venv
PY     := $(VENV)/bin/python
CONFIG := backend/pyproject.toml

.PHONY: install lint format test imports openapi openapi-check secrets check run seed-demo demo-receipts docker-check \
	eval-check eval eval-score

install:
	python3 -m venv $(VENV)
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e "./backend[dev,eval]"

lint:
	$(VENV)/bin/ruff check --config $(CONFIG) backend eval tests scripts
	$(VENV)/bin/ruff format --check --config $(CONFIG) backend eval tests scripts

format:
	$(VENV)/bin/ruff check --fix --config $(CONFIG) backend eval tests scripts
	$(VENV)/bin/ruff format --config $(CONFIG) backend eval tests scripts

test:
	$(PY) -m pytest -c $(CONFIG) --rootdir . -m "not integration" --cov=app --cov=eval --cov-report=term-missing tests

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

# Demo data (decision 0022): only loads into an empty database. TODAY=YYYY-MM-DD pins the date.
seed-demo:
	$(PY) -m app.demo_seed $(if $(TODAY),--today $(TODAY))

# Synthetic receipt photos for scenario 1 (committed in data/demo/receipts/; needs Pillow).
demo-receipts:
	$(PY) scripts/make_demo_receipts.py

# Needs docker, so run it on the host (the dev container has none).
docker-check:
	scripts/docker-health-check.sh

# Evaluation (F11, decision 0028; docs/wiki/backend/evaluation.md). The data in data/eval/
# is git-ignored. `make eval EVAL_ARGS="--config gemma3:4b@v3 --cases c02,c05"`.
eval-check:
	$(PY) -m eval.run_eval check

eval:
	$(PY) -m eval.run_eval run $(EVAL_ARGS)

# Offline: rescores the cache, rewrites eval/results/ and the report block.
eval-score:
	$(PY) -m eval.run_eval score $(EVAL_ARGS)
