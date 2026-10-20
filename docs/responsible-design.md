# Responsible design

This page covers how Budgie handles limitations, failure modes, privacy, security and misuse, and how AI-generated results are made identifiable (criterion 19 of the brief). It links to the decisions behind each point. The numbers come from the evaluation of 2026-10-14 ([evaluation](evaluation.md)).

## AI results are identifiable
- **On the review page**, every merchant, date, currency and total the model extracted carries an **"AI-generated"** badge until the user edits or confirms it ([0018](wiki/decisions/0018-review-form-editing-semantics.md)).
- **In the data**, every expense has a `source`: `ai`, `ai_corrected` (the user changed it) or `manual`. The API returns it, the Expenses page shows it in words, and the CSV export has it as a column ([csv-export](wiki/contracts/csv-export.md)).
- **Raw output is kept.** The raw model output, the model name and the prompt version are stored with each receipt (redacted), so any AI value can be traced back.
- **Nothing counts until it's confirmed.** Budgets and potential leaks use only expenses the user has confirmed. Confirming is refused while an item has no category.
- **Machine-made text is labelled.** Potential-leak texts are fixed templates filled with computed numbers; no model writes them. They are labelled "potential leak", because a rule can only flag a pattern ([0023](wiki/decisions/0023-leak-rules-and-thresholds.md)).
- **No probabilities.** The UI and the API show statuses and reasons, never a confidence value ([0008](wiki/decisions/0008-rule-based-review-status-not-probability.md), [uncertainty](wiki/backend/uncertainty.md)).

## Limitations and failure modes
| Failure mode | What happens | How Budgie handles it |
|---|---|---|
| Wrong value read from the photo | `qwen2.5vl:3b` got 7 % of totals, 18 % of dates and 11 % of merchants wrong | Rule checks flag sum, date and missing-field problems; the photo is shown next to the form; the user confirms every expense |
| Wrong but internally consistent extraction | the item sum matches a wrong total, so no rule fires | Not caught by code: in the evaluation, 3 wrong qwen extractions passed the extraction checks and were only flagged because of an uncategorised item. Documented as the main gap ([uncertainty](wiki/backend/uncertainty.md)) |
| Many false alarms | 13 of 14 fully correct qwen receipts were flagged, mostly because of `uncategorized_item` | Accepted trade-off: recall stays 100 %. Saved category choices reduce it over time |
| Not a receipt | the model may still claim it is one | A code plausibility rule checks too; the receipt fails with `not_a_receipt` and offers Retry or Enter manually, never the model's data ([0015](wiki/decisions/0015-non-receipt-is-a-failure-with-retry-or-manual-entry.md)) |
| Model server down, timeout, malformed output, unreadable image, database error | the extraction can't finish | Typed failures with reasons, one retry or one repair turn, a health endpoint, and a UI that keeps working ([ai-extraction](wiki/backend/ai-extraction.md#failure-handling-criterion-10)) |
| Latency | 40–130 s per receipt locally | Asynchronous extraction with progress polling ([0007](wiki/decisions/0007-async-extraction-with-polling.md)) |
| Narrow scope | German receipts only; no handwritten or English cases were evaluated; 30 cases | Stated in the README and the evaluation; other languages would need new cases and probably prompt changes |
| Leak rules are heuristics | thresholds such as "80 % used by mid-month" are fixed choices, not personal advice | Texts say "at this pace" and "about", never "you will"; every number is shown so the user can judge it |

## Privacy
- **Everything is local.** The model runs on the user's own machine (Ollama) and no data goes to a cloud API. The app makes no outgoing calls except to the configured model server.
- **Data minimisation, in two layers** ([0017](wiki/decisions/0017-personal-data-is-redacted-by-code.md)):
  - The extraction schema has no field for card, account, address or staff data, and the prompt says not to copy them.
  - Deterministic code also redacts IBANs, card numbers (Luhn-checked), masked digits and labelled ids from item texts, the merchant and the stored raw output. It also cuts addresses and phone numbers off the merchant name.
- **Storage is private.**
  - The database and the photos live on one volume with mode `700`, owned by a non-root user.
  - Images are served only through the API.
  - Raw model output, prompts and image bytes are never logged, and storage errors are logged without SQL parameters.
- **The user stays in control.** Deleting an expense also deletes its receipt photo, and the export contains only what the user confirmed.
- **Development data:**
  - The real receipts used for the evaluation are not in the repository (`data/eval/`, `data/receipts/` and upload folders are git-ignored).
  - The demo receipts are synthetic, with fictional shops ([0022](wiki/decisions/0022-demo-data-by-explicit-command.md)).
  - Committed test fixtures pass an automated personal-data scan.
  - In F08 a real receipt photo was once committed by mistake, which led to the git-ignore rule for every upload folder and the rule "stage files by name" ([workflow](wiki/plan/workflow.md#git-conventions)).

## Security
- **Prompt injection.**
  - Text on a receipt is data. The system prompt says so, and the output must match a fixed JSON schema, so an injected instruction can at most change field values, not trigger actions. The model has no tools and no access to anything else.
  - The evaluation still found that `qwen2.5vl:3b` followed 2 of 5 injected receipts (`gemma3:4b` v3: 0 of 5). Both injected receipts ended `needs_review`, so the user saw the flag.
  - The protection is the code (the sum and date rules, the plausibility rule, the non-item guard) plus the user's confirmation, not the model's obedience ([0030](wiki/decisions/0030-default-model-qwen2-5vl-3b.md)).
- **No login, so localhost only.** Budgie assumes one local user, as the brief allows. Compose publishes the port on `127.0.0.1` only; anyone who can reach the port can read and change all data, so it must not be exposed without an authenticating proxy.
- **Ollama has no login either.** On a Linux host, the README binds it to the docker bridge address, never to `0.0.0.0`, which would let anyone on the network use the model server.
- **Input handling.**
  - Uploads are limited by type (JPEG/PNG/WebP) and size (`MAX_UPLOAD_MB`, 10 MB).
  - The redaction rules read at most 65,536 characters and are tested against ReDoS inputs.
  - The model's output is validated against a schema before use, and its amounts are range-checked.
  - The UI never inserts text as HTML (a test checks for `innerHTML`-style sinks).
- **CSV export.** Values that a spreadsheet would run as a formula (`=`, `+`, `-`, `@`, …) are guarded ([0024](wiki/decisions/0024-csv-format-and-formula-guard.md)).
- **Container.** It runs as a non-root user, holds no secrets (`LLM_API_KEY` is a dummy), and takes all settings from environment variables. CI runs gitleaks over the whole history.
- **Supply chain.**
  - Python dependencies are pinned to ranges, and ruff exactly.
  - The Ollama image is pinned (`0.35.1`).
  - The model is pinned by tag only, and a tag can be re-pushed. The Ollama library listed `qwen2.5vl:3b` as manifest `fb90415cde1e` on 2026-10-20; `ollama list` shows the local digest.
- **Agent-assisted development.** The coding agent ran in a hardened dev container without credentials, and every push went through the student and protected CI ([0011](wiki/decisions/0011-sandbox-hardening.md), README "Sandboxed agent setup").

## Potential misuse
| Misuse | Mitigation |
|---|---|
| Treating extracted values as correct without checking | Badges, flags with reasons, the photo next to the form, and no confirmation without a category; the README and this page state the error rates |
| Reading potential-leak cards as financial advice | Wording says "potential", shows the numbers and the rule; no recommendation to buy or not buy |
| Processing other people's receipts or personal documents | Redaction removes payment and contact data; data stays local; deleting an expense deletes the photo |
| Forging expense records (e.g. for reimbursement) | Budgie is a personal budgeting tool, not a proof of purchase: the `source` field and the stored raw output show what came from a photo and what was typed or corrected, and no export claims to be a receipt |
| Exposing the service on a network | Localhost-only port in compose; no login is documented as a limit |
| Using the research-licensed default model commercially | The README and [0030](wiki/decisions/0030-default-model-qwen2-5vl-3b.md) state the Qwen Research License; `LLM_MODEL=gemma3:4b` switches to a model whose terms allow commercial use |
