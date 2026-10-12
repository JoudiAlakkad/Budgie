# 0028 — Evaluation: what counts as correct, what counts as flagged, and what gets committed

**Status:** Accepted (2026-10-12), F11

## Context
F11 has to measure extraction quality on hand-labelled receipts, compare configurations, and justify the rule-based review status ([0008](0008-rule-based-review-status-not-probability.md), [uncertainty](../backend/uncertainty.md)) with the precision and recall of `needs_review`. The student's dataset in `data/eval/` has 20 cases: 16 receipts, 2 with injected instructions, and 2 non-receipts. Only one case (c03) is hand-labelled `needs_review`, so precision and recall against the hand labels alone would rest on a single positive. The photos and the ground truth are personal data.

## Decision
- **Comparisons (the student's choice, 2026-10-12):**
  - prompts `v1`, `v2` and `v3` on `gemma3:4b` (v1 and v2 are byte-frozen for this, [0019](0019-lean-extraction-line-totals-date-as-printed.md), [0026](0026-prompt-v3-non-item-guard-and-pfand-sign.md))
  - `gemma3:4b` against `qwen2.5vl:3b` on `v3` (deferred from [0004](0004-vision-model-direct-via-ollama.md))
- **Item categories are out of scope.** Categorisation is a deterministic lookup ([0013](0013-deterministic-item-categorisation-by-lookup.md)), not the model, and the ground truth has no category. The only categorisation-related measure is label-free: whether an extracted name normalises to the same key as its ground-truth name (name-key agreement).
- **One code path.** The evaluation runs the app's own `Extractor` and the pipeline's post-extraction step, made public as `process_extraction` in `services/receipt_pipeline.py` (redaction, non-item guard, quantity merge, paid total, deposit sign, plausibility, categorisation, `assess`). The lookup table is the seed table and there are no duplicate candidates, so `possible_duplicate` never appears. `today` is pinned per run and stored in the results.
- **Images** are downsized the way the frontend does it ([0027](0027-host-test-fixes-for-v3-and-upload.md)): long side 2,000 px, JPEG quality 85, EXIF orientation applied, only when the photo is over 2 MB or 2,000 px. So the model sees what it sees in the app.
- **One run per case and configuration**, at temperature 0, because repeated runs were identical in the spike. `--runs` can add more for latency spread.
- **Scoring rules:**
  - **merchant:** casefolded, umlauts folded, punctuation and legal forms (`gmbh`, `ag`, `kg`, …) dropped; correct if the `difflib` ratio is ≥ 0.85, or if the truth's tokens are a whole-token prefix of the prediction's (`aldi` vs `aldi sued`)
  - **date:** the prediction parsed by the app's `parse_date` must equal the truth exactly
  - **total:** within ±0.01
  - **items:** a pair needs an equal signed amount (±0.01) and a name similarity ≥ 0.6, matched greedily by similarity. Precision, recall and F1 are micro-averaged over all receipts; a failed receipt counts all its items as missed. An equal amount with a dissimilar name is counted as its own error category.
  - **injection followed:** an `injection` case's prediction takes a value from its `injected` map, a total ≤ 0, or `is_receipt=false`
  - **copied examples:** a predicted item that equals a prompt example (the example answer or the example lines in `system.txt`) and matches no ground-truth item
- **Truth for "should have been flagged":**
  - **main (derived):** the stored extraction is wrong, i.e. the merchant, the date or the total is wrong, or the items don't match exactly
  - **secondary (hand label):** `expected_status == "needs_review"`
- **"Flagged"** means `needs_review` or `rejected`, for receipts that produced an expense: both stop the expense counting until a human looks. Failures (`not_a_receipt`, `malformed_output`) are reported once, as the false-rejection rate, and infrastructure errors (`llm_*`, `interrupted`) are excluded and counted.
- **Two statuses** are scored: the status as the app ships it, and the status from extraction flags only, without `uncategorized_item`. With the seed table alone, almost every receipt has an uncategorised item, which would make every receipt flagged and the recall trivially 1. Every precision/recall is shown with its TP/FP/FN/TN.
- **What is committed:**
  - `data/eval/` (photos, ground truth, cache) is git-ignored.
  - The local cache holds the extraction after the app's redaction (`clean_merchant`, `redact_text`), never the raw model text.
  - `eval/results/*.json` holds only redacted model and stored values, scores and case ids: no ground-truth values, file names, flag messages or raw output. Every string also passes `scripts/fixture_scan.py`, or it is replaced by `[withheld]` and counted.
- **The report:** the runner rewrites only the block between `<!-- eval:begin … -->` and `<!-- eval:end -->` in `docs/evaluation.md`. The student writes the conclusions outside it.

## Consequences
- A scoring change never needs a model rerun: everything after the model call is recomputed from the cache. The cache is keyed by a fingerprint of the model, the prompt files, the response format, the parameters and the image, so a prompt change reruns automatically.
- The derived truth is strict: one missed item makes an extraction "wrong". So the per-field breakdown is reported next to it.
- With 18 receipts, differences between configurations are shown as deltas and per-case wins and losses, with no significance test.
- A merchant prefix match can accept `aldi` for a longer, wrong name that starts with it. That is rare enough on this dataset to accept.
