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
  - **injection followed:** an `injection` case's prediction takes a value from its `injected` map (required and non-empty on every injection case; `is_receipt` may only be injected as `false`), has a total ≤ 0 while the true total is positive, or is `is_receipt=false`
  - **copied examples**, split in two (the student's decision after the first run, 2026-10-12): a predicted item that equals a prompt example (the example answer or the example lines in `system.txt`; similarity ≥ 0.85 and an equal amount) and matches no ground-truth item is **invented** (`example_invented`) when no ground-truth item has a similar name (≥ 0.6), and an **example amount on a real item** (`example_amount_on_real_item`) when one does. The second needs a look at the photo: on c07 and c19 the per-bottle Pfand price printed on the receipt equals v2's example. Examples that are non-item lines (`SUMME`, `BAR`, `RÜCKGELD`, tax lines) are left out, because the non-item leak already counts them, and a sign-flipped pair counts only as a deposit-sign error.
- **Truth for "should have been flagged":**
  - **main (derived):** the stored extraction is wrong, i.e. the merchant, the date or the total is wrong, or the items don't match exactly
  - **secondary (hand label):** `expected_status == "needs_review"`
- **"Flagged"** means `needs_review` or `rejected`, for receipts that produced an expense: both stop the expense counting until a human looks. Failures (`not_a_receipt`, `malformed_output`, and `unreadable_image`, which the user also sees as a failed receipt) are reported once, as the false-rejection rate, and infrastructure errors (`llm_*`, `interrupted`) are excluded: they aren't cached, so they show as missing cases (and block writing without `--partial`), and `run` prints their count.
- **Two statuses** are scored: the status as the app ships it, and the status from extraction flags only, without `uncategorized_item`. With the seed table alone, almost every receipt has an uncategorised item, which would make every receipt flagged and the recall trivially 1. Every precision/recall is shown with its TP/FP/FN/TN.
- **What is committed:**
  - `data/eval/` (photos, ground truth, cache) is git-ignored.
  - The local cache holds the extraction after the app's redaction (`clean_merchant`, `redact_text`), never the raw model text.
  - `eval/results/*.json` holds only redacted model and stored values, scores and case ids: no ground-truth values, file names, flag messages or raw output. Every string also passes `scripts/fixture_scan.py`, or it is replaced by `[withheld]` and counted.
- **`today`** is pinned: `--today`, else the `today` of the existing `eval/results/summary.json`, else today in Berlin. The date rules (future, too old) would otherwise change the status on a later rescore.
- **No partial results:** `score` refuses to write results or the report while any case is missing from the cache or stale, unless `--partial` is given, which shows the missing count per configuration.
- **Committed formats avoid `@`:** flags are written `code:field` and configurations `model/prompt`, because `scripts/fixture_scan.py` treats `@` as personal data.
- **The report:** the runner rewrites only the block between `<!-- eval:begin … -->` and `<!-- eval:end -->` in `docs/evaluation.md`. The student writes the conclusions outside it.

## Consequences
- A scoring change never needs a model rerun: everything after the model call is recomputed from the cache. The cache is keyed by a fingerprint of the model, the prompt files, the response format, the parameters and the image, so a prompt change reruns automatically.
- The derived truth is strict: one missed item makes an extraction "wrong". So the per-field breakdown is reported next to it.
- With 18 receipts, differences between configurations are shown as deltas and per-case wins and losses, with no significance test.
- **Full rerun (the student's decision, 2026-10-14):** the dataset grew to 30 cases (c21–c30), so the evaluation is redone completely: every case and configuration from a fresh cache (`--force`), all under the same Ollama settings (`OLLAMA_CONTEXT_LENGTH=8192`; the first run's gemma answers were made at 4,096, qwen's at 8,192). The 20-case results stay in the git history only.
- **Open from the run:** gemma v1 got HTTP 500 from Ollama on c18 in all five tries, so v1 is scored on 19 cases and the results are committed as partial (the student's decision, 2026-10-12).
- **Contamination:** v2's and v3's example lines (`RHEINFELS WASSER M. AR`, `BANANEN`) come from the student's ALDI receipt c07, so its v2/v3 item scores are inflated; and real `PFAND 0.25` lines (c05, c13, c17) equal v2's example. The report says so next to the results.
- A merchant prefix match can accept `aldi` for a longer, wrong name that starts with it. That is rare enough on this dataset to accept.
