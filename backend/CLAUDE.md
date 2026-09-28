# Backend conventions

Read first: `docs/wiki/backend/README.md`, `docs/wiki/contracts/`, `docs/wiki/architecture.md`.

- **Stack:** Python 3.12, FastAPI, Pydantic v2, pydantic-settings, SQLAlchemy 2.x, httpx, pytest. Lint and format with ruff.
- **Layers:** `api → services → domain + db + ai`.
  - `domain/` is pure: no imports from `db`, `ai` or `api`.
  - Only `db/` imports SQLAlchemy.
  - `api/` returns Pydantic DTOs from `api/schemas.py`, never ORM objects.
  - `import-linter` enforces these rules.
- **Config:** read everything through `app.config.Settings`, loaded from env vars. Never hard-code URLs, paths or keys.
- **Model calls:** use the OpenAI-compatible HTTP API through `ai/client.py` (httpx). Don't use a vendor SDK.
- **Errors:** raise typed errors (`LLMUnavailable`, `LLMTimeout`, `MalformedOutput`, `StorageError`, …). `api/errors.py` maps them to the error format in `docs/wiki/contracts/error-format.md`.
- **Tests** live in `/workspace/tests/`:
  - every domain function gets table-driven unit tests
  - AI tests use the recorded responses in `tests/fixtures/recorded_responses/` or a fake client
  - live-model tests are marked `@pytest.mark.integration`
- **Contracts:** don't change `api/schemas.py` or the endpoint shapes without the main session's approval. Propose the change in your report instead.
