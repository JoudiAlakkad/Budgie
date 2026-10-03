# Modules

```
backend/
├── app/
│   ├── main.py            # app factory: routers, error handlers, static mount, startup
│   ├── config.py          # pydantic-settings Settings, read from env (configuration.md)
│   ├── errors.py          # typed errors shared by all layers (StorageError, …)
│   ├── openapi_export.py  # python -m app.openapi_export --out docs/openapi.json [--check]
│   ├── api/
│   │   ├── schemas.py     # public DTOs, the contract
│   │   ├── errors.py      # error format and exception → HTTP mapping
│   │   └── receipts.py, expenses.py, item_categories.py, budgets.py (budgets and goal), insights.py, health.py
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
- The contracts in `backend/pyproject.toml`:
  - layers: `api > services > (domain | db | ai)`
  - "domain is pure": forbids everything app-internal except `app.errors`, plus `httpx`, `fastapi`, `pydantic_settings` and `sqlalchemy`; checks indirect imports too
  - "api does not import db or ai": direct imports only, because `api → services → db` is the intended path
  - "only app.db imports sqlalchemy": applies to all of `app`, ignoring `app.db` and `app.db.**`; direct imports only. It needs `unmatched_ignore_imports_alerting = "none"`, because `**` matches only submodules.
- Dependencies use compatible ranges with the current version as the lower bound. ruff is pinned exactly, to match `.pre-commit-config.yaml`.

## Stub routes
From F2 every documented endpoint exists as a route with its final signature ([0016](../decisions/0016-api-representation-and-stub-convention.md)). Until its feature is built, the body is `raise NotImplementedYet("F05")`, which answers `501 not_implemented`. A feature replaces only the body with a service call, so the spec doesn't change. Routers import only `app.api.schemas`, `app.api.errors`, `app.errors` and `app.services`.

Services receive the LLM client and the repositories through FastAPI dependencies, so tests can swap in fakes.
