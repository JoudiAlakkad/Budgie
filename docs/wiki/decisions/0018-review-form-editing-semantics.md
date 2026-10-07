# 0018 — Review form editing semantics

**Status:** Accepted (2026-10-06)

## Context
F06 builds `PATCH /expenses/{id}`, `POST /expenses/{id}/confirm`, `DELETE /expenses/{id}` and the Upload and Review pages. The contract fixes the shapes but leaves open how an edit changes `source`, what happens to a stored item category when the item is sent again, and how the UI labels AI-generated values: `Expense` has only an expense-level `source`, no per-field provenance. F07 isn't built yet, so every extracted item arrives `uncategorized`, and the review form's category dropdown is the only way to make an expense confirmable.

## Decision
- **Source:** a PATCH turns `ai` into `ai_corrected`. `ai_corrected` and `manual` stay as they are.
- **Unreadable fields:** each field sent in a PATCH is dropped from `unreadable_fields` (`line_items` when the items are sent), so its `unreadable` flag clears on reassessment.
- **Item category on PATCH:**
  - `category` sent → stored with `category_source: user`.
  - not sent, existing item (by `id`) with an unchanged description → the stored category and source stay.
  - otherwise (new item, or changed description) → the categorizer decides, as on create.
  - A user's choice is stored on the item only. Saving it to the lookup table for the next receipt is F07 (done implicitly on PATCH/POST, [0020](0020-user-category-choices-and-duplicate-rule.md)).
- **Foreign item ids:** a line item `id` that doesn't belong to the expense is `422 validation_error` with `fields` pointing at `line_items.<i>.id`.
- **AI badge is client-side, no contract change:** while the expense is unconfirmed and `source` is `ai`, every non-null extracted value shows an "AI-generated" badge; editing a field removes its badge for the page session. A reloaded `ai_corrected` expense shows one expense-level note ("AI-extracted, corrected by you — check the remaining values") instead of per-field badges. `manual` and confirmed expenses show none.
- **Explicit Save** (replaced by Save & confirm and Save draft in [0019](0019-lean-extraction-line-totals-date-as-printed.md)): the review form sends one PATCH when the user clicks **Save** (only the changed scalar fields, plus the full `line_items` list if any item changed) and re-renders flags and status from the response. Confirm is disabled while there are unsaved changes.
- **Pages in F06:** Upload and Review only. The Expenses page (list, filter, delete) moves to F10 with the CSV export.

## Consequences
- No change to `api/schemas.py` or `docs/openapi.json`; the frontend and backend agents can work in parallel ([0009](0009-contract-first-parallel-development.md)).
- After a reload, the UI can't say which fields of an `ai_corrected` expense are still the model's. The CSV `source` column stays expense-level anyway. A per-field `edited_fields` list in the contract would fix both and was rejected for now as more contract change than the criterion needs.
- Flags are stale between an edit and Save; the Save button and the disabled Confirm make that visible.
- Until F07, a user categorises every item of every receipt by hand (superseded by [0020](0020-user-category-choices-and-duplicate-rule.md)).
- A changed description recategorises the item, so the review page sends a `user` category again with the new description (found in F06 review).
- **Known limits (F06 review):**
  - Clearing an item's `qty` doesn't stick: an explicit `null` falls back to what the normaliser reads from the description. (`unit` is no longer on the form since 0019.)
  - An `unreadable` flag on `payment_method` can't be cleared, since it isn't an editable field; the expense stays `needs_review`. Confirm ignores flags, so nothing is blocked.
  - A PATCH that sends unchanged values still turns `ai` into `ai_corrected` and un-confirms. The page sends only changed fields, so only API clients hit this.
  - The badge also marks values the server filled in, not the model: the `EUR` default and the normaliser's `qty`/`unit`.
