# Pages

The pages are planned and built in F6, F8 and F9. Each one is a plain HTML file with its own JS module.

| Page | File | Purpose | Endpoints |
|---|---|---|---|
| Upload | `index.html` | drag and drop a photo, see extraction progress | `POST /receipts`, then poll `GET /receipts/{id}` |
| Review | `review.html?id=` | image and editable form side by side; flags and uncategorised items highlighted; confirm | `GET /receipts/{id}`, `GET /receipts/{id}/image`, `PATCH /expenses/{id}`, `POST /expenses/{id}/confirm` |
| Expenses | `expenses.html` | list, filter, delete, export | `GET /expenses`, `DELETE /expenses/{id}`, `GET /expenses/export.csv` |
| Dashboard | `dashboard.html` | spend vs. budget per category, goal progress, leak cards | `GET /insights/summary`, `GET /insights/leaks` |
| Settings | `settings.html` | budgets, savings goal, item-category table | `GET/PUT /budgets`, `GET/PUT /goal`, `/item-categories` |

## Review page rules
- AI-extracted values carry an **"AI-generated"** badge until the user edits or confirms them (criterion 19).
- Each flag is shown next to its field with its `message`. The page never shows a confidence percentage ([0008](../decisions/0008-rule-based-review-status-not-probability.md)).
- `uncategorized` items show a required category dropdown, and the Confirm button stays disabled until every item has one ([0013](../decisions/0013-deterministic-item-categorisation-by-lookup.md)).
- `category_source` is shown as a small hint: "from your earlier choice" or "default".
- A failed receipt shows the error `detail` and a Retry button ([receipt-lifecycle](../contracts/receipt-lifecycle.md)).
