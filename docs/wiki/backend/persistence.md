# Persistence

The data is stored in SQLite through SQLAlchemy, and only `app/db/` touches it ([0006](../decisions/0006-sqlite-behind-repository-layer.md)).

## Location
- `DATABASE_URL` is `sqlite:////data/budgie.db` in the container. Local development uses `./data/budgie.db`, which is git-ignored.
- Images go in `UPLOAD_DIR` (default `/data/uploads/`). The file names are random UUIDs, never the uploaded file name.
- Both live on the Docker volume `budgie-data`.

## Schema (draft, finalised in F1 and F2)
| Table | Columns |
|---|---|
| `receipts` | id, image_path, status, error, uploaded_at, model_name, prompt_version, raw_model_output, latency_ms |
| `expenses` | id, receipt_id?, merchant, date, currency, subtotal?, tax?, total, source, review_status, confirmed, flags (JSON), created_at, updated_at |
| `line_items` | id, expense_id, description, normalized_name, qty?, unit?, unit_price?, amount, category, category_source |
| `item_categories` | normalized_name (PK), category, source (`seed`/`user`), updated_at |
| `budgets` | category (PK), monthly_limit |
| `savings_goal` | id=1, target_amount, target_date, monthly_income? |

- Tables are created on startup with `metadata.create_all`. There is no migration tool, which is fine for the scope of this project.
- The seed rows for `item_categories` are inserted when the table is empty.
- Deleting a receipt removes its image file, its expense and its line items. `item_categories` stays.

## Error mapping
- A `SQLAlchemyError` or `OSError` in a repository raises `StorageError`, which the API returns as `500 storage_error` ([error format](../contracts/error-format.md)).
- `/health` runs `SELECT 1` and reports `db: ok|error`.
