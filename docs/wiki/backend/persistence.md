# Persistence

The data is stored in SQLite through SQLAlchemy, and only `app/db/` touches it ([0006](../decisions/0006-sqlite-behind-repository-layer.md)).

## Location
- `DATABASE_URL` is `sqlite:////data/budgie.db` in the container. Local development uses `./data/budgie.db`, which is git-ignored.
- Images go in `UPLOAD_DIR` (`/data/uploads/` in the container, `./data/uploads` locally). The file names are random UUIDs, never the uploaded file name.
- Both live on the Docker volume `budgie-data`.

## Schema (draft, finalised in F1 and F2)
| Table | Columns |
|---|---|
| `receipts` | id, image_path, status, error, uploaded_at, model_name, prompt_version, raw_model_output, latency_ms |
| `expenses` | id, receipt_id?, merchant?, date?, currency, subtotal?, tax?, total?, source, review_status, confirmed, flags (JSON), created_at, updated_at |
| `line_items` | id, expense_id, description, normalized_name, qty?, unit?, unit_price?, amount, category, category_source |
| `item_categories` | normalized_name (PK), category, source (`seed`/`user`), updated_at |
| `budgets` | category (PK), monthly_limit |
| `savings_goal` | id=1, target_amount, target_date, monthly_income? |

- Stored **redacted** by the pipeline in F05 ([0017](../decisions/0017-personal-data-is-redacted-by-code.md)): `expenses.merchant` through `domain.redaction.clean_merchant`, and `line_items.description` and `receipts.raw_model_output` through `redact_text`.
- Tables are created on startup with `metadata.create_all`. There is no migration tool, which is fine for the scope of this project.
- F1 builds only the engine, the session factory and `Base` (`db/session.py`, `db/models.py`). Each table arrives with the feature that first uses it.
- The seed rows for `item_categories` are inserted when the table is empty.
- Deleting a receipt removes its image file, its expense and its line items. `item_categories` stays.

## Error mapping
- A `SQLAlchemyError` or `OSError` in a repository raises `StorageError`, which the API returns as `500 storage_error` ([error format](../contracts/error-format.md)).
- `/health` runs `SELECT 1` and reports `db: ok|error`.
- **Startup never crashes on storage.** The lifespan calls `services.storage.prepare_storage`, which runs two steps independently: the upload dir (create it, then write a temp file to prove it's writable) and `init_db()`. Failures are logged and returned as a `StorageStatus` on `app.state.storage_status`.
- `current_health` reads that status. If startup failed, `/health` reports `db: error` without pinging, and it stays that way until a restart. Otherwise it runs `SELECT 1`.
- `Database` builds its engine on first use. A malformed URL, an unknown dialect, a bad port or a missing driver (e.g. `postgresql://` without psycopg) becomes `StorageError` from `init_db`, `ping` or `session_factory`.
- Typed errors (`StorageError`, later the LLM errors) live in `app/errors.py`, outside the layers, so `db` and `ai` can raise them and `api` can map them without importing either.
