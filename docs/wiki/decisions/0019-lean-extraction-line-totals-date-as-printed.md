# 0019 — Lean extraction: line totals, date as printed, Pfand

**Status:** Accepted (2026-10-06)

## Context
In the first run of F06 on the host, the student found the review page showed more than a user needs (subtotal, tax, unit, unit price), and the model mixed up per-unit and per-line prices. The user cares what was bought, how many, and what the line cost: "Kuchen, qty 3, 4,99 €". The prompt also asked the small model to transcribe *and* reformat the date, and it didn't tell a Pfand charged for bottles from a Pfand returned.

## Decision
- **Per item, the model extracts only the name (`description`), `qty` and `amount`, the total for the line.** v2 reads an item as a block of one or more lines grouped by the layout, so the amount is the total printed for the block, wherever its quantity line sits (refined 2026-10-06). `unit_price` is removed from the model schema.
- **`subtotal` and `tax` are no longer extracted.** They also leave `unreadable_fields`. The API fields stay (`api/schemas.py` is unchanged) and are `null` for AI expenses; manual entry through the API may still set them.
- **The date is transcribed as printed** (`14.03.26`, `14.03.2026`), without the time. `domain.validation.parse_date` turns it into a date; an unknown format stays `date_unparseable`.
- **Pfand:** a Pfand line for bottles bought (Pfand, Einwegpfand, Mehrweg) is its own item with a **positive** amount, a cost. Leergut, Pfandrückgabe, Pfandbon or Pfand zurück is money back: its own item with a **negative** amount. Until F07, `PlaceholderCategorizer` categorises names containing `pfand` or `leergut` as `deposit` (source `seed`).
- **Prompt `v2`** carries these rules and becomes the default `PROMPT_VERSION`. `v1` stays for the F11 comparison; it describes the old schema, which the model server no longer enforces.
- **Review page:** merchant, date, currency, total; items with name, quantity, amount and category. No per-item AI badge (the scalar badges and the `ai_corrected` note from [0018](0018-review-form-editing-semantics.md) stay).
- **Save & confirm → home:** the main button saves, confirms and returns to the upload page. If confirm is refused, the page stays, shows the saved expense with its flags and the reason. **Save draft** saves without confirming and returns home. This replaces 0018's single Save button.

## Consequences
- The sum check for AI expenses compares items with the total only; there is no hidden subtotal flag the user couldn't fix.
- Recorded fixtures still contain `unit_price`, `subtotal` and `tax`; `extra="ignore"` parses them, and the pipeline no longer reads those keys.
- After Save & confirm the user doesn't see recomputed flags unless confirm is refused. Flags never block confirm, so a `needs_review` expense can be confirmed in one click: the user reviewed it against the photo.
- A positive Pfand is counted under `deposit`, which isn't spending ([domain-logic](../backend/domain-logic.md#categories)), so bottle deposits don't inflate the drinks budget.
- v1 vs v2 on the same images is a natural prompt comparison for F11.
