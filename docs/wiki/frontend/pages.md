# Pages

The pages are planned and built in F6 (Upload, Review), F8 and F9 (Dashboard, Settings) and F10 (Expenses, [0018](../decisions/0018-review-form-editing-semantics.md)). Each one is a plain HTML file with its own JS module.

| Page | File | Purpose | Endpoints |
|---|---|---|---|
| Upload | `index.html` | drag and drop a photo, see extraction progress | `POST /receipts`, then poll `GET /receipts/{id}` |
| Review | `review.html?id=` | image and editable form side by side; flags and uncategorised items highlighted; confirm; retry or manual entry for a failed receipt | `GET /receipts/{id}`, `GET /receipts/{id}/image`, `POST /receipts/{id}/extract`, `POST /expenses`, `PATCH /expenses/{id}`, `POST /expenses/{id}/confirm` |
| Expenses | `expenses.html` | list, filter, delete, export | `GET /expenses`, `DELETE /expenses/{id}`, `GET /expenses/export.csv` |
| Dashboard | `dashboard.html` | spend vs. budget per category, goal progress, leak cards | `GET /insights/summary`, `GET /insights/leaks` |
| Settings | `settings.html` | budgets, savings goal, item-category table | `GET/PUT /budgets`, `GET/PUT /goal`, `/item-categories` |

## Review page rules
- AI-extracted values carry an **"AI-generated"** badge until the user edits or confirms them (criterion 19). The badge is tracked client-side ([0018](../decisions/0018-review-form-editing-semantics.md)): with `source: ai` every non-null value is badged and an edit removes its badge for the page session; a reloaded `ai_corrected` expense shows one expense-level note instead.
- Edits are sent with an explicit **Save** (one PATCH: the changed scalar fields, plus the full `line_items` list if any item changed); flags and the review status are re-rendered from the response. Confirm is disabled while there are unsaved changes. Merchant, date and total can't be sent empty (the API refuses `null`), so the page blocks that with a hint.
- A `null` field is shown as an explicit "unknown, please fill in", not left blank ([uncertainty](../backend/uncertainty.md)).
- Each flag is shown next to its field with its `message`. The page never shows a confidence percentage ([0008](../decisions/0008-rule-based-review-status-not-probability.md)).
- `uncategorized` items show a required category dropdown, and the Confirm button stays disabled until every item has one ([0013](../decisions/0013-deterministic-item-categorisation-by-lookup.md)).
- `category_source` is shown as a small hint: "from your earlier choice" or "default".
- A failed receipt shows its `error_detail` and two actions ([0015](../decisions/0015-non-receipt-is-a-failure-with-retry-or-manual-entry.md)):
  - **Retry** (`POST /receipts/{id}/extract`), except for `unreadable_image`. For `not_a_receipt` the hint says a retry with the same model usually gives the same result.
  - **Enter manually**: an empty form next to the photo, saved with `POST /expenses` and `receipt_id`. For `not_a_receipt` the page says the image wasn't recognised as a receipt; the model's data is never offered as a pre-fill.
