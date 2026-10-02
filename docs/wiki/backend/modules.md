# Modules

```
backend/
├── app/
│   ├── main.py            # app factory: routers, static mount, exception handlers, startup
│   ├── config.py          # pydantic-settings Settings, read from env (configuration.md)
│   ├── errors.py          # typed errors shared by all layers (StorageError, …)
│   ├── openapi_export.py  # python -m app.openapi_export --out docs/openapi.json [--check]
│   ├── api/
│   │   ├── schemas.py     # public DTOs, the contract
│   │   ├── errors.py      # error format and exception → HTTP mapping
│   │   └── receipts.py, expenses.py, item_categories.py, budgets.py, insights.py, health.py
│   ├── services/          # dependencies.py (FastAPI providers), health.py, storage.py; later receipt_pipeline.py, expenses.py, insights.py
│   ├── domain/            # pure: validation, confidence, categorize, duplicates, budget, leaks
│   │   └── data/item_categories_seed.yaml
│   ├── ai/                # client.py, extractor.py, schema.py, prompts/*.txt
│   ├── db/                # models.py, session.py, repositories/*.py
│   └── seed.py            # python -m app.seed, loads the demo data through services
├── pyproject.toml         # deps, ruff, pytest, import-linter config
tests/                     # at the repo root: unit/, api/, fixtures/recorded_responses/

At the repo root: Makefile (install, check, run, openapi, docker-check),
Dockerfile, .github/workflows/ci.yml, scripts/docker-health-check.sh.
```

## Dependency direction
The rules are enforced by `import-linter` ([0006](../decisions/0006-sqlite-behind-repository-layer.md)).
- `api` → `services` → (`domain`, `db`, `ai`)
- `domain` imports nothing from the app. It works on plain dataclasses or Pydantic models.
- `api` never imports `db` or `ai`, and `db` never imports `api`.
- The contracts in `backend/pyproject.toml`: a layers contract `api > services > (domain | db | ai)`; "domain is pure" (checks indirect imports too); "api does not import db or ai" and "only app.db imports sqlalchemy", which check direct imports only, because `api → services → db` is the intended path.

Services receive the LLM client and the repositories through FastAPI dependencies, so tests can swap in fakes.
