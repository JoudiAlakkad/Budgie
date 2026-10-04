# AI spike (milestone 2)

Run on 2026-10-03 to check [0004](../decisions/0004-vision-model-direct-via-ollama.md) and [0007](../decisions/0007-async-extraction-with-polling.md) before F02 fixes the contracts. Script: `scripts/ai_spike.py`. Raw outputs are in `data/spike/`, which is git-ignored because receipts are personal data.

## Setup
- Ollama 0.35.1 on the student's host (Apple Silicon Mac, so probably GPU via Metal, not the CPU-only laptop 0004 assumes), reached from the dev container at `http://host.docker.internal:11434/v1`.
- `gemma3:4b` (Q4_K_M, 3.3 GB), temperature 0, `max_tokens` 1024.
- 6 phone photos:
  - five German receipts (ALDI, Netto, Action, TEDi, a fuel station), about 4284×5712
  - one photo that is **not a receipt** (`24C13256…`, 3840×2160 landscape): a pinboard with a coloured mandala, handwritten notes and a shortcut card. It tests `is_receipt=false`.
- Two variants:
  - **base:** the draft schema from [ai-extraction](ai-extraction.md), with only `is_receipt`, `line_items` and `unreadable_fields` required. Two runs per photo.
  - **strict:** every key required (nullable), `unreadable_fields` limited to schema key names, and a prompt that defines merchant, date, total and line items. One run per photo.

## Results
| | base | strict |
|---|---|---|
| Valid JSON (all 6 photos) | 5/6 | 6/6 |
| Non-receipt recognised (`is_receipt=false`) | 0/2 runs | 0/1 |
| Merchant, date, total present (5 receipts) | rarely; usually omitted | 5/5 |
| Date in ISO format (5 receipts) | 0/5 | 4/5 |
| Items sum to total (5 receipts) | 1/5 | 1/5 |
| Latency, model loaded | 5–54 s | 28–76 s |
| Cold start | +15 s | – |
| Runaway output | 1 photo, about 100 s, every run | none |

- At temperature 0 the output was identical across runs.
- **Runaway output:** in base mode, `IMG_1554` repeated a URL inside `unreadable_fields` until the 1024-token limit.

## Accuracy, checked against two photos
- **Short, flat receipt (ALDI):**
  - merchant and date right; 7 of 8 item prices right
  - total read from the tax table (15.15 instead of the 15.17 paid)
  - counted the payment lines `ZU ZAHLEN`, `BAR` and `ZURÜCK` as items
  - read the tax-class digit after each price as a quantity
- **Long receipt (Netto):** item names were made up or garbled, the date was invented (2023-03-26 instead of 2026-09-22), the total was wrong (30.17 instead of 15.26), and the card payment line was counted as an item.
- **Likely cause:** Gemma 3 shrinks every image to about 896×896 (about 256 image tokens). On a long receipt photographed with lots of background, the print gets too small to read, and the model fills the gaps instead of reporting `null`.

## The non-receipt photo
The model returned `is_receipt: true` in every run, although the prompt says to return `false` for anything that isn't a receipt.
- **base (both runs):** one item, "Mandala Coloring Book" at 1.00; merchant, date and total `null`.
- **strict:** a complete invented receipt: merchant "Red Rock Trading Post", date 2023-10-26, total 25.00 USD, items "Mandala Coloring Book" 12.99 and "Mandala Coloring Pencils" 9.99.
- The strict schema made it worse: once every key is required, the model fills them instead of leaving them `null`.
- Only the sum check would have flagged the strict run (22.98 against 25.00), and only by chance. The base runs have no total, so the check can't run.
- **Conclusion:** the `is_receipt` flag can't be trusted on its own to produce `not_a_receipt`.

## Consequences for F03 (extractor) and F04 (validation)
- **Schema:** every key required and nullable. `unreadable_fields` is an enum of schema keys. Cap `line_items` (e.g. 100 items).
- **Prompt rules:**
  - payment and summary lines (`SUMME`, `ZU ZAHLEN`, `BAR`, `ZURÜCK`, `Kartenzahlung`, change) are not items
  - the letter or digit after a price is a tax class, not a quantity
  - a `2 x 3,99` or weight line above an item belongs to that item
  - `total` is the amount paid, not a row of the tax table
- **Output cut off at the token limit** (`completion_tokens == max_tokens`) is `malformed_output`, not something to retry.
- **Configuration:**
  - the 11-item receipt used 695 output tokens, so `LLM_MAX_TOKENS` should be 2048
  - `LLM_TIMEOUT_S=120` is tight for long receipts on CPU; 180 is safer
  - done in F03: the defaults are now 2048 and 180 ([configuration](configuration.md))
- **F04 validation matters most:** the sum-versus-total and plausible-date checks would have flagged both wrong receipts. The model doesn't report what it couldn't read, so `unreadable_fields` can't be trusted to mark the risky fields.
- **Non-receipt detection** needs more than the model's `is_receipt`. Untested options for F03/F04 (decided in F03: the plausibility rule, in F04; see [ai-extraction](ai-extraction.md)):
  - a separate yes/no classification call before extraction
  - a rule that treats a result with no merchant, no total and at most one item as `not_a_receipt`
- **Image preprocessing** (crop to the receipt, or split long receipts) is a candidate improvement for F03 or F11.

## Fixtures
- The raw outputs (truncated loop, missing keys, extra items, invented date, `is_receipt=true` on a non-receipt) are the failure cases F03 needs as recorded responses.
- They contain personal data (store addresses, partial card numbers in some receipts). F03 generates the fixtures in `tests/fixtures/recorded_responses/` with `scripts/make_fixtures.py`. It reads only the strict-schema outputs (ALDI and the pinboard photo) and redacts them with the app's own redaction module; the base-schema failures are rebuilt as synthetic cases ([0017](../decisions/0017-personal-data-is-redacted-by-code.md)). The raw files are never copied, and nothing is anonymised by hand or by an agent.

## Open
- The model comparison planned in 0004 (`qwen2.5vl:3b`, which keeps more image resolution) was not run in the spike. It moves to the F11 evaluation.
