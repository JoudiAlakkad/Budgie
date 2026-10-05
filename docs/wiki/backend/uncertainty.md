# Uncertainty

How Budgie treats uncertainty in the model's output (criterion 13 of the brief). In short: Budgie never shows a confidence value. Deterministic rules check each extraction, the result is one of three statuses with a reason for each flag, and the user confirms every expense.

## The brief's options, one by one
| Option in criterion 13 | Used? | How, or why not |
|---|---|---|
| Model-provided scores, where technically meaningful | no | `gemma3:4b` gives no score per field. The [spike](ai-spike.md) showed it is confidently wrong: it called a pinboard photo a receipt in every run, and the strict schema made it invent a shop, a date and a total. A self-reported score would have been high there too. |
| Agreement across repeated runs | no | At temperature 0 every spike run was identical, so agreement measures nothing. A signal that can disagree (two prompts or two models) is a comparison in F11, not a pipeline step ([0008](../decisions/0008-rule-based-review-status-not-probability.md)). |
| Validation against rules or source data | yes | The rules in [domain-logic](domain-logic.md#validationpy-f4): the item sum against subtotal or total, the date, required fields, the plausibility rule. Checking against the source, the photo, is the user's job: the review page shows the image next to the form. |
| Thresholds for accepting, flagging or rejecting | yes | Tolerance 0.02 per sum; a date no later than today and no more than 2 years old; the plausibility rule (no merchant, no total, at most one item → `not_a_receipt`). The result is `accepted`, `needs_review` or `rejected`. |
| Explicit "unknown" or escalation | yes | Unknown: a field the model can't read is `null`, and it may list it in `unreadable_fields`; the UI shows a `null` field as "unknown, please fill in". Escalation: every flag goes to the user with its reason next to the field; a non-receipt becomes `failed` with Retry or Enter manually ([0015](../decisions/0015-non-receipt-is-a-failure-with-retry-or-manual-entry.md)); nothing counts until the user confirms, and confirming is refused while an item is uncategorised. |

## No probability
- The API has no score field. `review_status` is a string Literal, and a test checks it is never a number.
- The brief allows a confidence value only if it is "appropriately justified or calibrated". Budgie doesn't show one. Instead F11 justifies the status: it reports the precision and recall of `needs_review` against the hand-labelled `expected_status`, i.e. how many wrong extractions get flagged and how many correct ones are flagged for nothing.

## What the user sees
- Each flag's `message` next to its field, e.g. "Items sum to 12.40 but total is 14.40".
- An "AI-generated" badge on each extracted value until the user edits or confirms it.
- Every field stays editable whatever the status. After each edit the rules run again, so a fixed field clears its flag.

## Known gaps
- An extraction that is internally consistent but wrong passes every rule, e.g. a misread price with a total that matches. Only the user's review catches it.
- `unreadable_fields` is weak: in the spike the model listed fields it had read correctly, and didn't list ones it had invented. A false `unreadable` flag only costs a review.
- The plausibility rule misses a non-receipt for which the model invents a full receipt. The sum and date checks usually flag it instead.

## Possible improvements
- Agreement between two prompt versions or two models, with a flag on each field where they disagree (costs a second call).
- A second, field-level pass on flagged fields only, e.g. re-reading the total from a crop.
- Cropping the receipt out of the photo, or splitting long receipts, before extraction. The spike's errors came mostly from small print after the model downscaled the image.
