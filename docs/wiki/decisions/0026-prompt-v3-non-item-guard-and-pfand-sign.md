# 0026 — Prompt v3, the non-item guard and the Pfand sign

**Status:** Accepted (2026-10-11)

## Context
On real receipts, prompt v2 still lets the model list **payment and tax lines as items**: SUMME, ZU ZAHLEN, BAR, GEGEBEN, Rückgeld, EC-Karte and rows of the MwSt table. The recorded `valid_receipt` (the spike's ALDI run) already showed it. The `sum_mismatch` flag catches it, but the user then deletes junk items by hand.

**Pfand** has two meanings: a cost charged for bottles bought, and money given back for bottles returned. v2 told them apart by keyword only. Some receipts print a return as plain `Pfand -0,25`, and many German tills print a negative amount with a trailing minus (`3,00-`), which a small model may drop.

## Decision
The student's decisions (2026-10-11), taking the plan's recommendations.
- **Prompt `v3`** becomes the default `PROMPT_VERSION`.
  - It is v2 plus an **item-area boundary**: items are only the lines above the first SUMME / ZU ZAHLEN / GESAMT line, and nothing below it is ever an item (payment, change, the tax table `MwSt`/`USt`/`Netto`/`Brutto`/`Steuer`, `A 19 %`, TSE and terminal lines), with short wrong/right examples.
  - **The Pfand rule puts the printed sign first:** copy each amount's sign exactly as printed, so `0,75-` and `-0,75` are both −0.75. The keywords (Pfand, Einwegpfand, Mehrweg = cost; Leergut, Pfandrückgabe, Pfandbon, Pfand zurück = return) only decide when no sign is printed.
  - The schema and the example answer are v2's (`OUTPUT_SPECS["v3"]` reuses them), so only the prompt text differs.
- **v2 is frozen** like v1 (a SHA-256 test), so v1, v2 and v3 can be compared fairly in F11.
- **Non-item guard (code):** `domain/non_items.py` `is_non_item(description)` matches whole words at the start of the folded description against a fixed list (`summe`, `zu zahlen`, `gesamt`, `bar`, `gegeben`, `rueckgeld`, `zurueck` but not `pfand zurueck`, `ec-karte`, `kartenzahlung`, `girocard`, `visa`, `mastercard`, `mwst`, `ust`, `netto`, `brutto`, `steuer`). The pipeline drops those items after redaction, before the facts are built. A prompt is never the only safety net for data handling.
- **Pfand sign (code):**
  - `is_deposit_return(description)` (whole words: `leergut`, `leergutbon`, `rueckgabe`, `pfandbon`, `pfand zurueck`) with a **positive** amount → the pipeline negates it. Those words always mean money back.
  - A plain deposit line is never changed. If the items don't match the total (> 0.02) and flipping **that one** item's sign makes them match (≤ 0.02), `assess` adds the flag **`deposit_sign_suspect`** on `line_items[i]`. The rules flag, the user decides ([0008](0008-rule-based-review-status-not-probability.md)).
  - The category stays `deposit` either way, which isn't spending, so the sign only affects the sum check and the total.
- **Raw output stays the model's answer.** The guard and the sign fix change the stored items, not `raw_model_output`, so F11 scores what the model said.

## Consequences
- `Flag.code` is a plain `str` in the API, so the new flag changes no schema; it is a new row in the [flag table](../backend/domain-logic.md#flag-codes) and in `FlagCode`.
- The guard can drop a real item whose name starts with a listed word (a product called "Bar…" is safe because only the whole word `bar` matches, but an item printed as `BAR` alone would go). The tests cover `BARILLA`, `Barbecue Sauce` and Pfand/Leergut lines.
- The stored items can differ from the raw output (fewer items, a flipped Leergut sign). The review page shows the stored items; the photo is beside them.
- v3 only helps if the model follows it. The student compares v2 and v3 on the problem receipts on the host. Without a sign printed and without a total, a wrong Pfand sign goes unnoticed.
- Amends the Pfand rules of [0019](0019-lean-extraction-line-totals-date-as-printed.md).
