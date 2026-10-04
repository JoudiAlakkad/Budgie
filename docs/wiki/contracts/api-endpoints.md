# API endpoints

- **Base URL (local):** `http://localhost:8000/api`
- **Interactive docs:** `http://localhost:8000/docs`
- **Spec file:** `docs/openapi.json`, generated from `backend/app/api/schemas.py` and checked for drift in CI.

All bodies are JSON unless stated otherwise. Errors use the [error format](error-format.md). Final since F2 ([0016](../decisions/0016-api-representation-and-stub-convention.md)).

## Conventions
- **Money** is a JSON number with at most 2 decimals (`1.99`). The server computes with `Decimal` and rounds to 0.01 before building a response; a value with more decimals is a server error. **Quantities** (`qty`) are JSON numbers without the 2-decimal limit (`1.234` kg) and may be negative (deposit returns).
- **Dates** are `YYYY-MM-DD`. **Timestamps** (`uploaded_at`, `updated_at`) are ISO 8601 in UTC, written with `Z`; a value stored without a zone is taken as UTC.
- **Months** (`?month=`) are `YYYY-MM` with a month from `01` to `12`.
- **Ids** are integers. **Currency** is three capital letters (`EUR`).
- **Responses** always contain every key; a missing value is `null`, never a left-out key.
- **Requests** reject unknown fields with `422 validation_error`.
- **Categories:** `Category` is the fixed list in [domain-logic](../backend/domain-logic.md#categories), including `deposit` and `discount`. `SpendingCategory` is the same list without those two. A line item's category can also be `uncategorized`.
- An endpoint that isn't built yet answers `501 not_implemented` with its final request and response shapes already in the spec.

## Receipts
| Method | Path | Request | Response |
|---|---|---|---|
| POST | `/receipts` | multipart `file` (jpeg/png/webp, ≤ `MAX_UPLOAD_MB`) | `202` `Receipt`, status `uploaded`; `413` `file_too_large`; `422` `unsupported_file` |
| GET | `/receipts` | `?status=` (optional) | `200` `Receipt[]`, newest first |
| GET | `/receipts/{id}` | – | `200` `Receipt`, with `expense` once one exists; `404` |
| GET | `/receipts/{id}/image` | – | `200` image bytes (`image/jpeg\|png\|webp`); `404` |
| POST | `/receipts/{id}/extract` | – | `202` `Receipt`, status `uploaded`; allowed from `failed` and `extracted`, and replaces an unconfirmed expense; `409` `invalid_state` otherwise; `404` |
| DELETE | `/receipts/{id}` | – | `204`, also deletes the image and its expense; `404` |

## Expenses
| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/expenses` | `?review_status=&confirmed=&from=&to=&category=` (all optional) | `200` `Expense[]`, newest date first |
| POST | `/expenses` | `ExpenseCreate` | `201` `Expense`; `409` `invalid_state` if `receipt_id` is set and that receipt isn't `failed`; `404` for an unknown `receipt_id` |
| GET | `/expenses/export.csv` | `?from=&to=` | `200` `text/csv` ([csv-export](csv-export.md)) |
| GET | `/expenses/{id}` | – | `200` `Expense`; `404` |
| PATCH | `/expenses/{id}` | `ExpenseUpdate` | `200` `Expense`; editing a confirmed expense un-confirms it; `404` |
| POST | `/expenses/{id}/confirm` | – | `200` `Expense`; `422` `uncategorized_items` or `incomplete_expense`; confirming twice is a no-op; `404` |
| DELETE | `/expenses/{id}` | – | `204`, also deletes its receipt and image; `404` |

- **Filters:** `category` (`Category` or `uncategorized`) matches expenses with at least one item in that category. `from` and `to` are inclusive dates. There is no pagination; the data is one user's.
- **Manual entry for a failed receipt:** `POST /expenses` with `receipt_id` attaches a hand-typed expense to the photo. The expense gets `source: manual`, and the receipt becomes `extracted` ([0015](../decisions/0015-non-receipt-is-a-failure-with-retry-or-manual-entry.md)).

## Item categories (the lookup table)
| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/item-categories` | `?q=` (substring of the name) | `200` `ItemCategory[]` |
| PUT | `/item-categories/{normalized_name}` | `{category: Category}` | `200` `ItemCategory`, source `user` |
| DELETE | `/item-categories/{normalized_name}` | – | `204`, removes a `user` entry so the seed applies again; `409` `invalid_state` for a seed entry; `404` |

`{normalized_name}` is the value from `LineItem.normalized_name`; the server doesn't normalise it again.

## Budgets, goal, insights, health
| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/budgets` | – | `200` `Budget[]` |
| PUT | `/budgets` | `Budget[]`, replaces the whole list | `200` `Budget[]` |
| GET | `/goal` | – | `200` `Goal`; `404` if no goal is set |
| PUT | `/goal` | `Goal` | `200` `Goal` |
| GET | `/insights/summary` | `?month=YYYY-MM` (default: current month) | `200` `InsightsSummary` |
| GET | `/insights/leaks` | `?month=YYYY-MM` (default: current month) | `200` `Leak[]` |
| GET | `/health` | – | `200` `{status, db: ok\|error, llm: ok\|down, model}`, always `200` while the app runs |

## DTOs
`?` marks a nullable value in a response, and an optional field in a request.

### Receipts and expenses
- **`Receipt`:** `{id, status: uploaded|extracting|extracted|failed|confirmed, uploaded_at, error?: ReceiptErrorCode, error_detail?: string, expense?: Expense}`. `error_detail` is a fixed, readable message per code ([error-format](error-format.md#receipt-error-codes)).
- **`Expense`:** `{id, receipt_id?, merchant?, date?, currency, total?, subtotal?, tax?, source: ai|ai_corrected|manual, review_status: accepted|needs_review|rejected, confirmed: bool, flags: Flag[], line_items: LineItem[]}`. `merchant`, `date` and `total` can be null after extraction; confirming requires them.
- **`LineItem`:** `{id, description, normalized_name, qty?, unit?, unit_price?, amount, category: Category|uncategorized, category_source: seed|user|none}`
- **`Flag`:** `{field?, code, message}`, e.g. `{field: "total", code: "sum_mismatch", message: "Items sum to 12.40 but total is 14.40"}`. `code` is a string; the known codes are listed in [domain-logic](../backend/domain-logic.md), so new rules don't change the contract.
- **`LineItemInput`:** `{id?, description, qty?, unit?, unit_price?, amount, category?: Category}`. The server derives `normalized_name` and `category_source`.
- **`ExpenseCreate`:** `{receipt_id?, merchant, date, currency = "EUR", total, subtotal?, tax?, line_items: LineItemInput[]}`, at least one line item. Created unconfirmed, with `source: manual` and a review status from the rules.
- **`ExpenseUpdate`:** every field of `ExpenseCreate` except `receipt_id`, all optional; only the fields sent change. A field that `ExpenseCreate` requires may be left out but not sent as `null`; `subtotal` and `tax` may be `null`. `line_items`, if sent, needs at least one item. `line_items`, if sent, replaces the list: items with an `id` are updated, items without one are created, missing ones are deleted. `source`, `review_status`, `confirmed` and `flags` are not writable.

### Settings and insights
- **`ItemCategory`:** `{normalized_name, category: Category, source: seed|user, updated_at}`
- **`Budget`:** `{category: SpendingCategory, monthly_limit}`, `monthly_limit > 0`
- **`Goal`:** `{target_amount, target_date, monthly_income?}`
- **`InsightsSummary`:** `{month, total_spent, total_budget?, projected_total, categories: CategorySpend[], goal?: GoalProgress}`
  - **`CategorySpend`:** `{category: SpendingCategory, spent, budget?, projected, state: under|on_pace_to_overrun|over}`
  - **`GoalProgress`:** `{target_amount, target_date, saved_this_month?, required_per_month, on_track?: bool}`
- **`Leak`:** `{type: recurring|over_budget|on_pace_to_overrun|spike|small_frequent, category?: SpendingCategory, merchant?, amount, explanation}`
