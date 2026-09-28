# 0006 — SQLite behind a repository layer

**Status:** Accepted (2026-09-28)

## Context
The brief requires the service to own its storage, with no direct access by anything else and all access through defined interfaces (criteria 4 and 8).

## Decision
- The data lives in SQLite (`/data/budgie.db`), and receipt images in `/data/uploads/`, both on the Docker volume `budgie-data`.
- Only `backend/app/db/` imports SQLAlchemy or knows table names. The layers are `api → services → domain + db`.
- The API returns only Pydantic DTOs, never ORM objects.
- CI runs `import-linter`: `app.api` and `app.domain` must not import `app.db`.
- The static mount serves only `frontend/`. Images are served only through `GET /api/receipts/{id}/image`.
- The container runs as a non-root user and `/data` has mode `700`. SQLite has no network port.

## Consequences
- The internal schema can change without breaking API consumers.
- Domain logic stays pure and easy to test.
- The details are in [architecture](../architecture.md) and [persistence](../backend/persistence.md).
