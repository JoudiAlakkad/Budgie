# 0015 — Non-receipt is a failure with retry or manual entry

**Status:** Accepted (2026-10-03)

## Context
The draft contract had two outcomes for "the image isn't a receipt": a failed receipt with `error: not_a_receipt`, and an expense with `review_status: rejected`. The [AI spike](../backend/ai-spike.md) showed the model's `is_receipt` can't be trusted: it called a photo of a pinboard a receipt in every run, and in strict mode invented a shop, date and total. A second, rule-based signal is needed, and the contract has to say where its result goes.

## Decision
- A non-receipt is a **failure**: the receipt becomes `failed` with `error: not_a_receipt`. It is set when the model reports `is_receipt=false` or when the plausibility rule (F03/F04, e.g. no merchant, no total and at most one item) decides the output isn't a receipt.
- The user chooses between two actions:
  - **Retry:** `POST /receipts/{id}/extract`.
  - **Enter manually:** `POST /expenses` with `receipt_id`. The expense gets `source: manual`, the receipt becomes `extracted`, and the photo stays linked. This works for any `failed` receipt.
- `review_status: rejected` now only means that required fields are missing; `is_receipt` is no longer one of its rules ([0008](0008-rule-based-review-status-not-probability.md)).

## Consequences
- "Not a receipt" and "a receipt with gaps" stay separate in the UI and the evaluation.
- At temperature 0 the same model gives the same answer, so Retry helps only after a model or prompt change. The UI must not suggest otherwise.
- A real receipt wrongly judged a non-receipt is not a dead end: the user types it in next to the photo. The data the model did extract is not offered as a pre-fill.
- Manual entry also covers the other failures (`llm_unavailable`, `interrupted`, …), so a receipt never blocks the user.
