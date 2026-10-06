# Pages

The pages are planned and built in F6 (Upload, Review), F8 and F9 (Dashboard, Settings) and F10 (Expenses, [0018](../decisions/0018-review-form-editing-semantics.md)). Each one is a plain HTML file with its own JS module.

| Page | File | Purpose | Endpoints |
|---|---|---|---|
| Upload | `index.html` | drag and drop a photo, recent receipts; after the upload it opens the review page, which shows the progress | `POST /receipts`, `GET /receipts` |
| Review | `review.html?id=` | image and editable form side by side; flags and uncategorised items highlighted; confirm; retry or manual entry for a failed receipt | `GET /receipts/{id}`, `GET /receipts/{id}/image`, `POST /receipts/{id}/extract`, `POST /expenses`, `PATCH /expenses/{id}`, `POST /expenses/{id}/confirm` |
| Expenses | `expenses.html` | list, filter, delete, export | `GET /expenses`, `DELETE /expenses/{id}`, `GET /expenses/export.csv` |
| Dashboard | `dashboard.html` | spend vs. budget per category, goal progress, leak cards | `GET /insights/summary`, `GET /insights/leaks` |
| Settings | `settings.html` | budgets, savings goal, item-category table | `GET/PUT /budgets`, `GET/PUT /goal`, `/item-categories` |

## Review page rules
- AI-extracted values carry an **"AI-generated"** badge until the user edits or confirms them (criterion 19). The badge is tracked client-side ([0018](../decisions/0018-review-form-editing-semantics.md)): with `source: ai` every non-null value is badged and an edit removes its badge for the page session; a reloaded `ai_corrected` expense shows one expense-level note instead.
- Edits are sent with an explicit **Save** (one PATCH: the changed scalar fields, plus the full `line_items` list if any item changed); flags and the review status are re-rendered from the response. Confirm is disabled while there are unsaved changes. Merchant, date and total can't be sent empty (the API refuses `null`), so the page blocks that with a hint.
- A `null` **required** field (merchant, date, total; an item's description and amount) is shown as an explicit "unknown, please fill in", not left blank ([uncertainty](../backend/uncertainty.md)). An empty item list says so too. Optional nulls (subtotal, tax, an item's qty, unit and unit price) are an empty input with a quiet "–": most items have no unit, and the hint on every row was noise (the student's decision, 2026-10-06).
- Each flag is shown next to its field with its `message`. The page never shows a confidence percentage ([0008](../decisions/0008-rule-based-review-status-not-probability.md)).
- `uncategorized` items show a required category dropdown, and the Confirm button stays disabled until every item has one ([0013](../decisions/0013-deterministic-item-categorisation-by-lookup.md)).
- **Flags without an input** (F06): a flag on `payment_method` goes to an "Other findings" list above the form, and a flag on `line_items` (no index) under the Line items heading. Flags address items as `line_items[i]`, validation errors as `line_items.i.field`; the page maps both.
- **Badges after Edit again:** confirming counts as reviewing every value, so a confirmed expense that is unlocked again shows no per-field badges; a later reload of an `ai_corrected` expense shows the expense-level note.
- **Category on Save:** an item's `category` is sent only for a new row or when the user changed it, so a stored `seed` category keeps its source. An item still `uncategorized` is sent without `category` (the contract doesn't accept that value). A `user` category is sent again when the item's description changes, or the server would recategorise it and the choice would be lost (found in review).
- **Dirty check:** inputs are trimmed, and stored text is compared trimmed (an empty string counts as empty), so an AI description like `"Milch "` doesn't make a fresh form unsaved.
- `category_source` is shown as a small hint: "from your earlier choice" or "default".
- A failed receipt shows its `error_detail` and two actions ([0015](../decisions/0015-non-receipt-is-a-failure-with-retry-or-manual-entry.md)):
  - **Retry** (`POST /receipts/{id}/extract`), except for `unreadable_image`. For `not_a_receipt` the hint says a retry with the same model usually gives the same result.
  - **Enter manually**: an empty form next to the photo, saved with `POST /expenses` and `receipt_id`. For `not_a_receipt` the page says the image wasn't recognised as a receipt; the model's data is never offered as a pre-fill.
