# 0026 — Prompt v3, the non-item guard and the Pfand sign

**Status:** Accepted (2026-10-11)

## Context
On real receipts, prompt v2 still lets the model list **payment and tax lines as items**: SUMME, ZU ZAHLEN, BAR, GEGEBEN, Rückgeld, EC-Karte and rows of the MwSt table. The recorded `valid_receipt` (the spike's ALDI run) already showed it. The `sum_mismatch` flag catches it, but the user then deletes junk items by hand.

**Pfand** has two meanings: a cost charged for bottles bought, and money given back for bottles returned. v2 told them apart by keyword only. Some receipts print a return as plain `Pfand -0,25`, and many German tills print a negative amount with a trailing minus (`3,00-`), which a small model may drop.

## Decision
The student's decisions (2026-10-11), taking the plan's recommendations.
- **Prompt `v3`** becomes the default `PROMPT_VERSION`.
  - It is v2 plus an **item-area boundary**: the items end at the line with the amount paid (SUMME / ZU ZAHLEN / GESAMT followed by the total), and nothing at or below it is ever an item (payment, change, the tax table `MwSt`/`USt`/`Netto`/`Brutto`/`Steuer`, `A 19 %`, TSE and terminal lines), with short wrong/right examples. A ZWISCHENSUMME or a column header (`Menge Artikel Preis Gesamt`) is not the boundary, and Pfand, Leergut and discount lines between a ZWISCHENSUMME and the amount paid are still items (found by the reviewer).
  - **The Pfand rule puts the printed sign first:** copy each amount's sign exactly as printed, so `0,75-` and `-0,75` are both −0.75. The keywords (Pfand, Einwegpfand, Mehrweg = cost; Leergut, Pfandrückgabe, Pfandbon, Pfand zurück = return) only decide when no sign is printed.
  - The schema and the example answer are v2's (`OUTPUT_SPECS["v3"]` reuses them), so only the prompt text differs.
- **v2 is frozen** like v1 (a SHA-256 test), so v1, v2 and v3 can be compared fairly in F11.
- **Non-item guard (code):** `domain/non_items.py` `is_non_item(description)` drops a line whose folded description starts with a payment, change or tax word. Words that are also product or shop names (`bar`, `visa`, `mastercard`, `total`, `netto`, `brutto`, `steuer`) count only when an amount alone follows, and tax-table rows (`A 19 % 1,66`) count too. The word lists are in [domain-logic](../backend/domain-logic.md#non_itemspy-f13). The pipeline drops those items after redaction, before the facts are built. A prompt is never the only safety net for data handling.
  - **Plausibility** ([0015](0015-non-receipt-is-a-failure-with-retry-or-manual-entry.md)) counts the dropped lines, because a SUMME line is evidence of a receipt, but without merchant and total at least one real item must be left (found by `/code-review`: dropping first rejected `BROT` + `SUMME`).
- **Pfand sign (code):**
  - `is_deposit_return(description)` (whole words such as `leergut`, `rueckgabe`, `pfandbon`, `pfand zurueck`, `flaschenrueckgabe`; the full list is in domain-logic) with a **positive** amount → the pipeline negates it. Those words always mean money back.
  - A plain deposit line is never changed. If the items don't match the total (> 0.02) and flipping **that one** item's sign makes them match (≤ 0.02), `assess` adds the flag **`deposit_sign_suspect`** on `line_items[i]`. The rules flag, the user decides ([0008](0008-rule-based-review-status-not-probability.md)). Only a **negative** return word is exempt, so a positive Leergut typed by hand or in an edit is flagged too. The message says which way the flip goes ("may be a return" / "may be a charge"); when several lines would match, each gets "One of these deposit lines may have the wrong sign" (found by `/code-review`).
  - The category stays `deposit` either way, which isn't spending, so the sign only affects the sum check and the total.
- **Raw output stays the model's answer.** The guard and the sign fix change the stored items, not `raw_model_output`, so F11 scores what the model said.

## Consequences
- `Flag.code` is a plain `str` in the API, so the new flag changes no schema; it is a new row in the [flag table](../backend/domain-logic.md#flag-codes) and in `FlagCode`.
- The guard can drop a real item whose name starts with a listed word followed by nothing but an amount (an item printed as `BAR 1,99`). Product names such as `Bar-Riegel`, `VISA Geschenkkarte 50`, `NETTO Spülmittel` and `BARILLA` stay; the tests list them. The sum check flags a dropped real amount.
- **Known limit:** if the model put the total only in the items (`SUMME 12.40`) and left `total` null, the guard drops the only copy, and the expense is flagged `missing_total`; the raw output still has the line.
- The stored items can differ from the raw output (fewer items, a flipped Leergut sign). The review page shows the stored items; the photo is beside them.
- v3 only helps if the model follows it. The student compares v2 and v3 on the problem receipts on the host. Without a sign printed and without a total, a wrong Pfand sign goes unnoticed.
- Amends the Pfand rules of [0019](0019-lean-extraction-line-totals-date-as-printed.md).
