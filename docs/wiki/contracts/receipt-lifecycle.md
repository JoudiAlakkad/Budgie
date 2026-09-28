# Receipt lifecycle

```
uploaded ──► extracting ──► extracted ──(user confirms)──► confirmed
                  │              │
                  ▼              └─ expense.review_status: accepted | needs_review | rejected
               failed ──(POST /extract)──► extracting
```

| Status | Meaning | UI behaviour |
|---|---|---|
| `uploaded` | the image is stored and extraction is queued | show a spinner and poll every 2 s |
| `extracting` | the model is running | spinner and poll |
| `extracted` | an expense exists with a `review_status` and flags | open the review form |
| `failed` | extraction failed; `error` says why (`llm_unavailable`, `llm_timeout`, `malformed_output`, `not_a_receipt`, `unreadable_image`) | show the reason and a Retry button |
| `confirmed` | the user confirmed the expense, and it counts in budgets and insights | read-only, with an option to edit again |

## Rules
- **Only confirmed expenses count.** Unconfirmed `extracted` receipts don't count toward budgets or leaks.
- **Confirming needs every item categorised.** It fails with `422 uncategorized_items` otherwise ([0013](../decisions/0013-deterministic-item-categorisation-by-lookup.md)).
- **`review_status` is a rule result, not a probability** ([0008](../decisions/0008-rule-based-review-status-not-probability.md)):
  - `rejected` means the image isn't a receipt, or required fields are missing and can't be fixed. The user can still enter the fields by hand.
  - `needs_review` means at least one flag is set.
- **Restarts:** on startup, receipts stuck in `extracting` are set to `failed` with `error: interrupted`.
- **Timing:** `POST /receipts` returns `202` right away, and the UI polls `GET /receipts/{id}` ([0007](../decisions/0007-async-extraction-with-polling.md)).
