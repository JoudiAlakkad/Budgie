# 0031 — The user can mark an `unreadable` flag as checked

**Status:** Accepted (2026-10-21, the student's decision, from the F12 host test)

## Context
In the F12 host test, an ALDI receipt showed "could not be read" warnings on date, currency, total, line items and payment method, although the values on the form were right. The model fills `unreadable_fields` itself, and the signal is weak: it lists fields it read correctly ([uncertainty](../backend/uncertainty.md)). Until now the only way to clear an `unreadable` flag was to *change* the field, because a PATCH drops only the fields it sends ([0018](0018-review-form-editing-semantics.md)), and the page sends only changed fields. A correct value kept its warning. `payment_method` has no input, so its flag could never be cleared.

The same screenshot showed a second, separate problem. "Items sum to 17.80 but total is 10.95" stayed after the user had typed 17.80, because flags only refresh from a server response (0018: "flags are stale between an edit and Save").

## Decision
- **Contract:** `ExpenseUpdate` gets `checked_fields: list[CheckedField]` (optional, default empty). Its keys are the model's unreadable-field keys: `merchant`, `date`, `currency`, `line_items`, `total`, `payment_method`. An unknown key is `422 validation_error`.
- **Semantics:** the checked keys leave `unreadable_fields`, then the rules run again. A key that isn't in `unreadable_fields` is ignored.
  - A body with only `checked_fields` changes no value. So it leaves `source` alone (`ai` stays `ai`), and it doesn't un-confirm.
  - A body with value changes as well behaves like any edit.
  - `checked_fields` sent together with values is allowed.
- **Only `unreadable` flags can be checked.** That flag is the model's self-doubt, and a human look settles it. Rule flags (`sum_mismatch`, `missing_*`, the deposit sign, `possible_duplicate`, …) are facts about the data ([0008](0008-rule-based-review-status-not-probability.md)). They clear only when the data is fixed. There is no dismiss button for them.
- **Review page:**
  - Every `unreadable` flag gets a **"Looks right"** button. A click replaces the flag with "Marked as checked" and an **Undo**, counts as an unsaved change, and sends the key in `checked_fields` with the next Save draft or Save & confirm.
  - As soon as the user edits a field, the flags on it are dimmed with "Edited — rechecked when you save". An item edit dims the `line_items` and `line_items[i]` flags and the `sum_mismatch` flags. A date or total edit also dims `possible_duplicate`. The response to the save re-renders all flags as before.

## Consequences
- The "payment_method flag can't be cleared" limit in 0018 is gone.
- A check-only PATCH keeps `source: ai`. So the CSV and the badge still say the values are the model's, which is true.
- The server doesn't check that the user really looked; the button is a statement, the same as Confirm.
- The page still has no client-side rule engine. The dimmed flags say "rechecked on save" instead of being recomputed live, so the rules stay in one place (the backend domain).
- Rejected: hiding `unreadable` when the value is non-null. The model sometimes puts a guess there, and the human check is the point.
- Rejected: sending checked fields as unchanged values in the existing PATCH. That would turn `ai` into `ai_corrected` for a value nobody corrected, and it can't express `payment_method`.
