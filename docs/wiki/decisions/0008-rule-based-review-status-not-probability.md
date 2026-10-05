# 0008 — Rule-based review status, not a probability

**Status:** Accepted (2026-09-28). Amended by [0015](0015-non-receipt-is-a-failure-with-retry-or-manual-entry.md): `is_receipt` no longer sets a review status; a non-receipt makes the receipt `failed`. Amended in F04 (2026-10-05): no tax check, and no run-to-run agreement; see below.

## Context
Criterion 13 asks the project to explain how uncertainty is treated, and says a confidence value "must not be presented as a probability unless it has been appropriately justified or calibrated". Small local models don't give meaningful calibrated scores.

## Decision
- Each receipt gets a status of `accepted`, `needs_review` or `rejected`, decided by deterministic rules:
  - arithmetic check (items ≈ subtotal, or ≈ total without a subtotal)
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

## Amendment (F04, 2026-10-05)
- **No tax check.** German receipts print VAT as included, so `subtotal + tax ≈ total` flags correct receipts (spike TEDi run: subtotal 3.10, tax 0.49, total 3.10). Tax is stored but not checked.
- **Run-to-run agreement is dropped** as a signal. At temperature 0 every spike run was identical, so agreement measures nothing. The original plan listed it; a signal that can disagree (two prompts or two models) moves to F11 as a comparison.
- The full answer to criterion 13 is in [uncertainty](../backend/uncertainty.md).
