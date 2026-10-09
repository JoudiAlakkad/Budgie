# Persistence

The data is stored in SQLite through SQLAlchemy, and only `app/db/` touches it ([0006](../decisions/0006-sqlite-behind-repository-layer.md)).

## Location
- `DATABASE_URL` is `sqlite:////data/budgie.db` in the container. Local development uses `./data/budgie.db`, which is git-ignored.
- Images go in `UPLOAD_DIR` (`/data/uploads/` in the container, `./data/uploads` locally). The file names are random UUIDs, never the uploaded file name.
- Both live on the Docker volume `budgie-data`.

## Schema
`receipts`, `expenses` and `line_items` are fixed in F05, `item_categories` in F07, `budgets` and `savings_goal` in F08.

| Table | Columns |
|---|---|
| `receipts` | id, image_path, image_type (`jpeg`/`png`/`webp`), status (indexed), error?, uploaded_at, model_name?, prompt_version?, latency_ms?, raw_model_output? |
| `expenses` | id, receipt_id? (unique FK), merchant?, date?, currency, subtotal?, tax?, total?, source, review_status, confirmed, flags (JSON), unreadable_fields (JSON), created_at, updated_at |
| `line_items` | id, expense_id, position, description, normalized_name, qty?, unit?, unit_price?, amount, category, category_source |
| `item_categories` | normalized_name (PK), category, source (`seed`/`user`), updated_at |
| `budgets` | category (PK), monthly_limit |
| `savings_goal` | id (`CHECK (id = 1)`, named `single_goal`), target_amount, target_date, monthly_income? |

- **Types:**
  - Money is stored as integer cents (a `MoneyCents` type decorator), because SQLite has no decimal type and `Numeric` goes through float.
  - `qty` is stored as decimal text (`DecimalText`), because it is unrounded.
  - Timestamps are naive UTC, which the DTOs read as UTC.
- `image_path` is the file name only (`<uuid4>.<ext>`), relative to `UPLOAD_DIR`. The image store checks that the resolved path stays inside `UPLOAD_DIR`.
- `error_detail` is not stored. The service derives it from `error` with a fixed map, so a wording fix also reaches old rows.
- `unreadable_fields` keeps the model's list, so F06 can recompute the flags and drop a key once the user edits it. `position` keeps the item order, so `line_items[i]` in a flag stays stable.
- Stored **redacted** by the pipeline in F05 ([0017](../decisions/0017-personal-data-is-redacted-by-code.md)): `expenses.merchant` through `domain.redaction.clean_merchant`, and `line_items.description` and `receipts.raw_model_output` through `redact_text`. In the raw output, `merchant` is also replaced by the cleaned value when the output parses as JSON ([ai-extraction](ai-extraction.md#pipeline-servicesreceipt_pipelinepy-f05)). A merchant entered by hand is stored as typed.
- Repositories return frozen record dataclasses (`db/records.py`), never ORM objects. Status changes are conditional updates (`WHERE id = :id AND status = :from`, where `from_` is one status or a collection, e.g. retry's `failed`/`extracted`), and the caller checks the row count. `ExpenseRepository.by_receipts(ids)` loads the expenses of a receipt list in one query.
- **Editing (F06):** `ExpenseRepository.update(id, ExpenseChanges)` writes the merged scalars, flags, `unreadable_fields`, `source`, `review_status` and `confirmed`, and replaces the item list: items with an id (`LineItemChange.id`) are updated in place, items without one inserted, the rest deleted; `position` is the list index. A foreign or repeated item id raises `ValueError`, which the service turns into `422`. `set_confirmed` is a bulk update. The service runs them together with the receipt transition in one transaction.
- **Ids are never reused:** the three tables use SQLite `AUTOINCREMENT` (`sqlite_autoincrement`). Without it, a retried receipt's new expense got the deleted expense's id, so a polling client could mistake new data for old.
- **The engine is built with `hide_parameters=True`,** so a logged `StorageError` traceback never shows SQL parameters such as merchants or descriptions ([0017](../decisions/0017-personal-data-is-redacted-by-code.md)).
- Tables are created on startup with `metadata.create_all`. There is no migration tool, which is fine for the scope of this project.
- F1 builds only the engine, the session factory and `Base` (`db/session.py`, `db/models.py`). Each table arrives with the feature that first uses it.
- **Seed sync (F07, [0020](../decisions/0020-user-category-choices-and-duplicate-rule.md)):** on startup, `prepare_storage` syncs `app/domain/data/item_categories_seed.yaml` once the tables exist: missing names are inserted, rows still `seed` take the YAML value, `user` rows and names no longer in the YAML stay. A missing or broken seed (OSError, YAMLError, `InvalidSeed`) is wrapped in `StorageError`; it is step `item_categories_seed` in `StorageStatus` (health `db: error`, so the docker gate catches a missing YAML) and never stops startup.
- **Saving a category** (`upsert_user`, `put_seed`) is one SQLite `INSERT … ON CONFLICT(normalized_name) DO UPDATE` with no read first, so two concurrent saves of the same new name can't collide; `updated_at` changes only when the category or source does. `DELETE` of a `user` entry with a seed value updates the row back to the seed entry. The statement is SQLite-specific ([0006](../decisions/0006-sqlite-behind-repository-layer.md)).
- **Budgets and goal (F08):** `BudgetRepository.replace` deletes and inserts inside the caller's transaction, so a failure (e.g. a repeated primary key) leaves the old list; `all()` is sorted by category. `GoalRepository.put` is one SQLite `INSERT … ON CONFLICT(id) DO UPDATE` without a read first.
- **Spend query (F08):** `ExpenseRepository.confirmed_spend_by_category(date_from, date_to)` is one `SUM … GROUP BY` over `line_items` joined to `expenses`: confirmed expenses with a date in the inclusive range, every item category, values as `Decimal` (the `MoneyCents` sum). The domain drops the non-spending categories ([0021](../decisions/0021-dashboard-and-goal-semantics.md)). **Visits query (F09):** `confirmed_visits_by_category(date_from, date_to)` counts distinct confirmed expenses per item category in the inclusive range (one expense with two eating-out items is one visit; one expense can count for two categories). `count()` on the expense, receipt and budget repositories serves the demo seed's emptiness check.
- **CSV export (F10):** no new query. `ExportService` reuses `ExpenseRepository.list(ExpenseFilter(confirmed=True, date_from, date_to))` in one read transaction; items arrive in `position` order through the relationship's `order_by`, and `domain/csv_export.render` re-sorts the newest-first list to date, expense id, position.
- `ExpenseRepository.duplicate_candidates(date, total, exclude_id)` returns a tuple of same-date expenses whose total is within a cent of the cent-rounded total; the merchant is compared in the domain.
- `ExpenseFilter.has_receipt` (F08): `False` adds `receipt_id IS NULL`, `True` adds `receipt_id IS NOT NULL` to the list query; it combines with the other filters. A manual entry attached to a failed receipt has a receipt. `Expense.created_at` in the API is the stored `expenses.created_at` (naive UTC, read as UTC).
- Deleting a receipt removes its image file, its expense and its line items. `item_categories` stays. `DELETE /expenses/{id}` with a receipt deletes through `ReceiptRepository.delete` (cascade), then the file; without a receipt only the expense rows go.
  - The rows go first (ORM cascade), then the file. If removing the file fails, that is logged and the answer is still `204`; an orphan file is harmless.
  - Images are written to a temp file, then moved in with `os.replace`. If the receipt row can't be created, the file is removed again.
  - **Known limit:** delete, retry and manual entry read before they write. Under SQLite's deferred transactions, that can fail at once with `SQLITE_BUSY` while the pipeline is writing its outcome. The user then sees a one-off `500 storage_error` and can just try again.
- **Startup reset:** `uploaded` and `extracting` receipts become `failed` with `interrupted` whenever the database step of startup succeeded (`StorageStatus.database_ok`), even if the upload dir failed. It never stops the app.

## Error mapping
- A `SQLAlchemyError` or `OSError` in a repository raises `StorageError`, which the API returns as `500 storage_error` ([error format](../contracts/error-format.md)).
- `/health` runs `SELECT 1` and reports `db: ok|error`.
- **Startup never crashes on storage.** The lifespan calls `services.storage.prepare_storage`, which runs its steps independently: the upload dir (create it, then write a temp file to prove it's writable), `init_db()` and, since F07, the seed sync. Each step catches `StorageError` only, so a programming error still surfaces. Failures are logged and returned as a `StorageStatus` on `app.state.storage_status`.
- `current_health` reads that status. If startup failed, `/health` reports `db: error` without pinging, and it stays that way until a restart. Otherwise it runs `SELECT 1`.
- `Database` builds its engine on first use. A malformed URL, an unknown dialect, a bad port or a missing driver (e.g. `postgresql://` without psycopg) becomes `StorageError` from `init_db`, `ping` or `session_factory`.
- Typed errors (`StorageError`, later the LLM errors) live in `app/errors.py`, outside the layers, so `db` and `ai` can raise them and `api` can map them without importing either.
