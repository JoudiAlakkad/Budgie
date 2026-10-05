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
│   ├── services/          # dependencies.py (FastAPI providers), health.py, storage.py, views.py (F05),
│   │                      # receipts.py, receipt_pipeline.py, categorization.py (seam until F07), expenses.py (F05); later insights.py
│   ├── domain/            # pure: redaction (F3), validation, confidence, categorize, duplicates, budget, leaks
│   │   └── data/item_categories_seed.yaml
│   ├── ai/                # client.py, extractor.py, schema.py, prompts/__init__.py (load_prompts) + prompts/<version>/{system,user,repair}.txt (package data)
│   ├── db/                # models.py, session.py, records.py, images.py (image store), repositories/*.py
│   └── seed.py            # python -m app.seed, loads the demo data through services
├── pyproject.toml         # deps, ruff, pytest, import-linter config
tests/                     # at the repo root: unit/, api/, integration/ (-m integration, live model),
                           # fixtures/recorded_responses/ (generated), recorded.py (replay helper)

At the repo root: Makefile (install, check, run, openapi, docker-check),
Dockerfile, .github/workflows/ci.yml, scripts/docker-health-check.sh,
scripts/make_fixtures.py (generates the redacted recorded responses, decision 0017) and
scripts/fixture_scan.py (independent privacy scan, used by the generator and the tests).
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
- Dependencies use compatible ranges with the current version as the lower bound. `python-multipart` is held below `0.1` (`>=0.0.32,<0.1`), because its 0.0.x releases have changed the API. ruff is pinned exactly, to match `.pre-commit-config.yaml`.

## Stub routes
From F2 every documented endpoint exists as a route with its final signature ([0016](../decisions/0016-api-representation-and-stub-convention.md)). Until its feature is built, the body is `raise NotImplementedYet("F05")`, which answers `501 not_implemented`. A feature replaces only the body with a service call, so the spec doesn't change. Routers import only `app.api.schemas`, `app.api.errors`, `app.errors` and `app.services`. This is checked in review; no import-linter contract enforces it.

Stub owners: receipts, and listing, creating and fetching expenses → F05 (until F05 the `POST /expenses` stub and `test_contract.py` said F06; F05 owns it because manual entry belongs to the receipt lifecycle); editing, confirming and deleting expenses → F06; item categories → F07; budgets, goal and insights summary → F08; leaks → F09; CSV export → F10.

**Frontend mount:** the static frontend is mounted at `/` with `FrontendMount`, a `Mount` that refuses `/api` and `/api/...`. Without it, Starlette preferred the static mount over a partial API match, so an unknown API path gave 405 and a wrong method gave 404.

**Upload form:** `POST /receipts` takes `ReceiptUpload`, a form model with one `file` field, so the spec has a stable schema name instead of FastAPI's generated `Body_…`.

**Money in responses:** services round every amount to 0.01 (`Decimal.quantize`) before building a DTO. A response with more decimals fails validation and becomes `500 internal_error`; this matters first for projections in F08.

Services receive the LLM client and the repositories through FastAPI dependencies, so tests can swap in fakes. From F05 this also covers `get_today`, the item categorizer, the image store and the extractor factory. A provider must not declare request parameters, or the spec would drift.

**Services return views:** a service returns plain view dataclasses (`services/views.py`) whose field names match the DTOs, and the router builds the DTO with `model_validate(view, from_attributes=True)`. `api/health.py` already follows this pattern.
