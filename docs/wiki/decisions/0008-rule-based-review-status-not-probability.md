# 0008 — Rule-based review status, not a probability

**Status:** Accepted (2026-09-28). Amended by [0015](0015-non-receipt-is-a-failure-with-retry-or-manual-entry.md): `is_receipt` no longer sets a review status; a non-receipt makes the receipt `failed`.

## Context
Criterion 13 says a confidence value must not be shown as a probability unless it is calibrated. Small local models don't give meaningful calibrated scores.

## Decision
- Each receipt gets a status of `accepted`, `needs_review` or `rejected`, decided by deterministic rules:
  - arithmetic checks (items ≈ subtotal, subtotal + tax ≈ total)
  - date plausibility
  - required fields present
  - fields the model reported as unreadable
  - `is_receipt`
  - uncategorised items
- The flags are stored with a human-readable reason, and the UI shows them next to the field.
- No percentage or score is shown.

## Consequences
- The status is explainable and testable.
- The evaluation measures how well `needs_review` catches wrong extractions (precision and recall of the flag).
- Optional agreement across repeated runs can be added as another rule later.
