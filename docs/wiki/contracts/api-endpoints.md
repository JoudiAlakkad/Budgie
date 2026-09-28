# API endpoints

- **Base URL (local):** `http://localhost:8000/api`
- **Interactive docs:** `http://localhost:8000/docs`
- **Spec file:** `docs/openapi.json`

All bodies are JSON unless stated otherwise. Errors use the [error format](error-format.md). This page is a draft and is finalised in F2.

## Receipts
| Method | Path | Request | Response |
|---|---|---|---|
| POST | `/receipts` | multipart `file` (jpeg/png/webp, ≤ `MAX_UPLOAD_MB`) | `202` `Receipt`, with status `uploaded`; `422` for a bad file |
| GET | `/receipts` | `?status=` | `200` `Receipt[]` |
| GET | `/receipts/{id}` | – | `200` `Receipt`, including `expense` once extracted; `404` |
| GET | `/receipts/{id}/image` | – | `200` image bytes; `404` |
| POST | `/receipts/{id}/extract` | – | `202`, which re-runs extraction (for `failed`, or to retry) |
| DELETE | `/receipts/{id}` | – | `204`, which also deletes the image and its expense |

## Expenses
| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/expenses` | `?from=&to=&category=&status=` | `200` `Expense[]` |
| POST | `/expenses` | `ExpenseCreate`, for manual entry without a receipt | `201` `Expense` |
| PATCH | `/expenses/{id}` | `ExpenseUpdate` (fields and line items; a line-item category is saved to `item_categories`) | `200` `Expense` |
| POST | `/expenses/{id}/confirm` | – | `200` `Expense`; `422` if any item is `uncategorized` |
| DELETE | `/expenses/{id}` | – | `204` |
| GET | `/expenses/export.csv` | `?from=&to=` | `200` `text/csv` ([csv-export](csv-export.md)) |

## Item categories (the lookup table)
| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/item-categories` | `?q=` | `200` `ItemCategory[]` |
| PUT | `/item-categories/{normalized_name}` | `{category}` | `200` `ItemCategory` (source `user`) |
| DELETE | `/item-categories/{normalized_name}` | – | `204` |

## Budgets, goal, insights, health
| Method | Path | Response |
|---|---|---|
| GET/PUT | `/budgets` | `Budget[]`, one `{category, monthly_limit}` per category |
| GET/PUT | `/goal` | `{target_amount, target_date, monthly_income?}` |
| GET | `/insights/summary?month=YYYY-MM` | spend per category vs. budget, projection, goal progress |
| GET | `/insights/leaks?month=YYYY-MM` | `Leak[]` `{type, category?, merchant?, amount, explanation}` |
| GET | `/health` | `{status, db: ok\|error, llm: ok\|down, model}`. Always `200` while the app runs. |

## Main DTOs (draft)
- **`Receipt`:** `{id, status, uploaded_at, error?, expense?: Expense}`
- **`Expense`:** `{id, receipt_id?, merchant, date, currency, total, subtotal?, tax?, source: ai|ai_corrected|manual, review_status: accepted|needs_review|rejected, confirmed: bool, flags: Flag[], line_items: LineItem[]}`
- **`LineItem`:** `{id, description, normalized_name, qty?, unit?, unit_price?, amount, category, category_source: seed|user|none}`
- **`Flag`:** `{field, code, message}`, for example `{field: "total", code: "sum_mismatch", message: "Items sum to 12.40 but total is 14.40"}`

Categories are a fixed list: see [domain-logic](../backend/domain-logic.md#categories).
