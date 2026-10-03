# Receipt lifecycle

```
uploaded ──► extracting ──► extracted ──(confirm)──► confirmed
                 │              ▲                        │
                 ▼              │ (enter manually)       │ (edit expense)
              failed ───────────┘                        ▼
                                                     extracted

Retry (POST /receipts/{id}/extract) from failed or extracted ──► uploaded
```

| From | Action | To |
|---|---|---|
| – | `POST /receipts` | `uploaded` |
| `uploaded` | the background task starts | `extracting` |
| `extracting` | an expense is extracted | `extracted` |
| `extracting` | extraction fails | `failed`, with a [receipt error code](error-format.md#receipt-error-codes) |
| `failed`, `extracted` | `POST /receipts/{id}/extract` | `uploaded`; an unconfirmed expense is deleted |
| `failed` | `POST /expenses` with `receipt_id` (enter manually) | `extracted`, with a `manual` expense |
| `extracted` | `POST /expenses/{id}/confirm` | `confirmed` |
| `confirmed` | `PATCH /expenses/{id}` | `extracted`; the expense is unconfirmed and must be confirmed again |
| `uploaded`, `extracting` | the app restarts | `failed`, `error: interrupted` |

Any other action returns `409 invalid_state`.

| Status | Meaning | UI behaviour |
|---|---|---|
| `uploaded` | the image is stored and extraction is queued | show a spinner and poll every 2 s |
| `extracting` | the model is running | spinner and poll |
| `extracted` | an expense exists with a `review_status` and flags | open the review form |
| `failed` | extraction failed; `error` and `error_detail` say why | show `error_detail` with **Retry** and **Enter manually** ([actions per code](error-format.md#receipt-error-codes)) |
| `confirmed` | the user confirmed the expense, and it counts in budgets and insights | read-only, with an option to edit again |

## Rules
- **Only confirmed expenses count.** Unconfirmed expenses don't count toward budgets or leaks. The receipt is `confirmed` exactly when its expense is confirmed.
- **Confirming needs a complete expense:** merchant, date and total set (`422 incomplete_expense`) and every item categorised (`422 uncategorized_items`, [0013](../decisions/0013-deterministic-item-categorisation-by-lookup.md)).
- **A non-receipt is a failure, not a review result** ([0015](../decisions/0015-non-receipt-is-a-failure-with-retry-or-manual-entry.md)). If the model reports `is_receipt=false`, or the plausibility rule decides the output isn't a receipt, the receipt is `failed` with `error: not_a_receipt`. The user can retry or enter the expense by hand.
- **`review_status` is a rule result, not a probability** ([0008](../decisions/0008-rule-based-review-status-not-probability.md)):
  - `rejected` means required fields are missing and the rules can't fill them. The user can still enter them by hand.
  - `needs_review` means at least one flag is set.
- **Timing:** `POST /receipts` and `POST /receipts/{id}/extract` return `202` right away, and the UI polls `GET /receipts/{id}` ([0007](../decisions/0007-async-extraction-with-polling.md)).
