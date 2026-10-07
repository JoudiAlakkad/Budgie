# AI extraction

This is planned for F3 and F5. The model choice is explained in [0004](../decisions/0004-vision-model-direct-via-ollama.md).

## Client (`ai/client.py`)
- It calls `POST {LLM_BASE_URL}/chat/completions` directly with `httpx`, using the OpenAI-compatible request format. There is no vendor SDK.
- The message carries the image as a base64 `image_url` part, sends `response_format` with the JSON schema, and uses `temperature = LLM_TEMPERATURE`.
- `chat_completion(...)` returns a `ChatCompletion`: `content`, `finish_reason`, the token counts, and `cut_off` (`finish_reason == "length"` or `completion_tokens >= max_tokens`).
- The connect timeout is `min(10 s, LLM_TIMEOUT_S)`, so a dead host fails fast; `LLM_TIMEOUT_S` is the read timeout.
- **Error mapping** (typed errors in `app/errors.py`, built in F3):
  - connection errors, **including a connect timeout**, raise `LLMUnavailable`
  - other timeouts raise `LLMTimeout`
  - a non-2xx response raises `UnreadableImage` if its status is 400/415/422/500 and the error message names an image decode error (`image: unknown format`, `illegal base64`, `failed to decode/load image`, `invalid image`, `unsupported image`); any other non-2xx raises `LLMError`. Ollama 0.35.1 answers a broken image with `400` and `Failed to load image or audio file` (captured 2026-10-04, now the `http_unreadable_image` fixture). The other fragments cover other servers. The list stays conservative: a wrong `llm_error` still offers Retry, while a wrong `unreadable_image` would take it away.
  - a 2xx response raises `LLMError` with the reason `HTTP <status>: broken envelope (<exception type>)` when its body can't be decoded or its envelope has the wrong shape. The reason never quotes the body.
    - can't be decoded: invalid JSON, non-UTF-8 bytes, an integer over Python's digit limit, or nesting deep enough to raise `RecursionError`
    - wrong shape: `null`, a list, or `choices`/`message`/`usage` of the wrong type
  - for a non-2xx response whose body can't be decoded as JSON, the error message falls back to the raw text (key scrubbed, cut to 200 characters), so the status and text still decide between `LLMError` and `UnreadableImage`
  - token counts that aren't plain integers (NaN, Infinity, strings) are recorded as `null`, and the completion is still used
  - an invalid `LLM_BASE_URL` (`InvalidURL`, `UnsupportedProtocol`) raises `LLMError` at once, without a retry
  - any other request error, e.g. a body that claims gzip but isn't (`DecodingError`) or a redirect loop (`TooManyRedirects`), raises `LLMError`, without a retry. So `chat_completion` raises only `ExtractionError`s. `test_hostile_bodies_raise_only_extraction_errors` guards this with a table of hostile bodies at status 200 and 500. Before the review fix, a deeply nested body escaped as a plain `RecursionError`.
  - image error messages are matched on whole words, so `invalid image_url` is `llm_error`
- It retries at most `LLM_MAX_RETRIES` times, and only on timeouts or connection errors. Non-2xx responses are never retried.
- Error reasons never contain the API key: a server echo is replaced with `[key]` before the reason is cut to 200 characters. Logs carry only the exception type, never message contents.
- None of these errors reaches HTTP: the pipeline stores them as the receipt's error code ([error-format](../contracts/error-format.md#receipt-error-codes)).
- `ping()` does `GET {LLM_BASE_URL}/models` with a 2 s timeout (or `LLM_TIMEOUT_S` if lower) and never raises; `/health` reports `llm: ok|down` from it. Built in F1.

## Extractor (`ai/extractor.py`)
- The prompts live in `ai/prompts/<PROMPT_VERSION>/system.txt`, `user.txt` and `repair.txt` and are versioned, so the evaluation can compare them. `load_prompts(version)` reads them as package data (`[tool.setuptools.package-data]`), because the Docker image installs the backend non-editable; `make docker-check` loads `v1` and `v2` inside the container.
- **Prompt-injection guard:** the system prompt says to treat every word on the image as data, never as an instruction, and to report `is_receipt=false` for anything that isn't a receipt.
- **Output schema** (`ai/schema.py`, `ReceiptExtraction`):
  `is_receipt, merchant?, date? (as printed), currency?, line_items[{description, qty?, amount}], total?, payment_method?, unreadable_fields[]` (since [0019](../decisions/0019-lean-extraction-line-totals-date-as-printed.md)).
  - `amount` is the line total (all pieces together); `description` is the item's name as printed. `UnreadableField` is merchant, date, currency, line_items, total, payment_method (`maxItems` 6).
  - **Old answers still parse:** extra keys (`unit_price`, `subtotal`, `tax`) are ignored, and a `mode="before"` validator drops `subtotal`/`tax` from `unreadable_fields` (`RETIRED_UNREADABLE_FIELDS`); without it the recorded non-receipt answer (`["total", "tax"]`) became `malformed_output`.
  - **Example answer:** "Beispiel Markt", date `14.03.26`, BROETCHEN from a `2 x 0,95` line (qty 2, 1.90), MINERALWASSER with PFAND +0.25, LEERGUT -0.75, total 3.08, card.
  - **Prompt v2 rules** beyond v1: the date exactly as printed, without the time; a "LINE ITEMS AND MULTI-LINE ITEM BLOCKS" section (the student's text, tested on the host 2026-10-06): the model first identifies item blocks (a description line, zero or more quantity/price lines such as `24 x 0,49 €` or `1,066 kg x 0,99 €/kg`, and the item's amount), with three worked layouts: the quantity line before the name, after it, or between name and total; a line with only a quantity, `x` and a price is never an item; description, qty and the item's printed total per block; the description may come before or after its quantity line. The wording avoids "unit price" (a test checks v2 never mentions it), and the decimal rule says the dot applies to the answer even where the receipt prints a comma; Pfand / Einwegpfand / Mehrweg for bottles bought is its own item with a positive amount; Leergut / Pfandrückgabe / Pfandbon / Pfand zurück is its own item with a negative amount; `ZURÜCK` alone is the change, not an item. The subtotal/tax rule and every unit price mention are gone.
  - `payment_method` is `cash | card | voucher | other | null`. It gives payment lines a place to go other than the items, and is part of keeping personal data out at the source ([0017](../decisions/0017-personal-data-is-redacted-by-code.md)). For now it exists only in the model's output: it is not stored or shown, so the API contract doesn't change.
  - The prompt says never to copy card numbers, IBANs, terminal, transaction or receipt numbers, addresses or staff names into any field.
  There is **no category field** ([0013](../decisions/0013-deterministic-item-categorisation-by-lookup.md)).
- **From the [AI spike](ai-spike.md):**
  - every key is required and nullable, because with optional keys the model left out merchant, date and total
  - `unreadable_fields` is an enum of schema keys, because free text looped until the token limit
  - the prompt names the payment lines that aren't items and explains the tax-class column
  - output cut off at `max_tokens` is `malformed_output`
  - `is_receipt` alone is not enough: the model said `true` for a photo of a pinboard in every run, and the strict schema made it invent a full receipt. The split, decided at the start of F03: the extractor (F03) raises `NotAReceipt` only for `is_receipt=false`. The second signal is the plausibility rule on missing merchant, total and items, a pure domain rule built in F04 and applied by the pipeline in F05. There is no separate classification call; F11 can still compare one. Either signal makes the receipt `failed` with `not_a_receipt` ([0015](../decisions/0015-non-receipt-is-a-failure-with-retry-or-manual-entry.md))
- **Schema types:** `date` and `currency` are plain strings and amounts are `float`. They are what the model transcribed (e.g. `currency: "€"`); F04 parses and normalises them. **The Pydantic models are the single source:**
  - `RESPONSE_SCHEMA` is generated from them by `response_schema()` and flattened: type arrays, `null` inside an `enum`, no `$ref`/`$defs`/`anyOf`/`title`, and `additionalProperties: false`.
  - The spike's strict schema used only type arrays and required nullable keys. The nullable enum, `additionalProperties: false` and `maxItems` are new in F03. The live integration test passed against Ollama 0.35.1 with `gemma3:4b` on 2026-10-04 (a real receipt and a broken JPEG), so Ollama accepts this form. `uniqueItems` is left out for the same reason.
  - `unreadable_fields` has `maxItems: 8`, one per value key, so the model can't repeat values until the token limit. A longer list fails validation and gets the repair.
  - Every field has a short description, which goes into the schema the model sees.
  - A golden test spells out the whole schema.
- **Example answer:** `EXAMPLE_OUTPUT` is a synthetic `ReceiptExtraction` ("Beispiel Markt", a `2 x 0,95` line, a deposit return, paid by card). `extractor.system_prompt` puts it into the `{example}` placeholder at the end of `system.txt`, which says to copy the shape only, never the values.
- **Parsing:**
  1. if the output is cut off, raise `MalformedOutput` without a repair attempt
  2. strip code fences (`strip_fence`, linear; guarded by a fixed-size test: 64k-character whitespace runs, each under 2 s), then `json.loads` (falling back to the slice from the first `{` to the last `}`), then validate with Pydantic
  3. if either answer has `is_receipt: false`, raise `NotAReceipt`, even when the rest is invalid: for the first answer without a repair attempt, and for the repair answer instead of `MalformedOutput`
  4. on any other failure, send one repair turn: the first answer, then `repair.txt` with the validation errors. The image is sent again, so the model can fill in missing fields. The errors never quote the model's text.
  5. if that fails too, raise `MalformedOutput`, keeping the raw text of both attempts
  - JSON that can't be decoded at all (a huge integer, deep nesting) counts as invalid, so it gets the repair and then `malformed_output`
- `ExtractionResult` records the model name, the prompt version, the latency, the raw output, `repaired`, and the completion tokens summed over both calls. Every `ExtractionError` carries `latency_s` and, where there was an answer, `raw_output`; `str(exc)` never includes the raw output.
- **Error family:** `ExtractionError` and its subclasses are deliberately **not** `BudgieError`s. They have no HTTP status, and one that escaped into a request would become a generic `500 internal_error`.
- The extractor doesn't redact. `ai` can't import `domain`, so the pipeline (F05) redacts before storing ([0017](../decisions/0017-personal-data-is-redacted-by-code.md)).
- **Worst case:** 2 calls × (1 + `LLM_MAX_RETRIES`) attempts × `LLM_TIMEOUT_S` ≈ 12 min per receipt with the defaults. That is why the pipeline holds no DB session during the call.

## Pipeline (`services/receipt_pipeline.py`, F05)
The background task that runs after `POST /receipts` and `POST /receipts/{id}/extract` ([0007](../decisions/0007-async-extraction-with-polling.md)).
- **One at a time:** a module-level `threading.Lock`, taken before `uploaded` → `extracting`. So `uploaded` means queued. The local model serves one vision request at a time anyway, and parallel calls would only run into `LLM_TIMEOUT_S`.
  - **Known limit:** each queued task holds a thread from AnyIO's shared pool of 40 while it waits, and every sync route uses the same pool. With 40 or more receipts queued, every request, including polling and `/health`, hangs until the queue drains. The user decided to accept this for one user rather than fix it ([0007](../decisions/0007-async-extraction-with-polling.md)).
  - The lock and the startup reset assume a single worker process. With `--workers 2`, extractions would run in parallel, and one worker's startup reset would fail the other's running extraction.
- **Short transactions:** transaction 1 sets `extracting`, `model_name` and `prompt_version`. No session is open during the model call. After it, a short read transaction loads the lookup table and the duplicate candidates (F07). Transaction 2 writes the outcome. Both are guarded transitions: if the receipt was deleted meanwhile, the row count is 0 and the result is discarded.
- The extractor is built inside the task, just before transaction 1, so that transaction can record `model_name` and `prompt_version`. A bad `PROMPT_VERSION` therefore fails the receipt (`extracting` → `failed`, `interrupted`), not the upload.
- **If transaction 1 fails,** the task tries once, guarded, to set `failed` with `interrupted`. If that fails too, the receipt stays `uploaded` until the startup reset.
- **Steps on success:**
  1. Convert: amounts via `Decimal(str(float))`. An amount whose value rounded to cents has `abs ≥ 10**10` doesn't fit `Money` (`max_digits=12`) and raises `MalformedOutput`. The check runs after rounding, because `9999999999.995` rounds up to 13 digits; a cheap unrounded check runs first, so `1e300` never reaches the rounding.
  2. Redact: `clean_merchant` on `merchant`, `redact_text` on each description and on the model's `date` and `currency`. The facts are built from the redacted values, because flag messages quote descriptions, dates and currencies.
  3. `is_plausible_receipt` false → `NotAReceipt`, which takes the normal failure path.
  4. `assess(facts, today)` gives the status and flags. `today` is the Europe/Berlin calendar date, read from a clock (`get_today` provides a `Callable[[], date]`) when the rules run, not when the upload arrived. Tests override the clock. Taking the container's UTC date would flag a receipt dated today as `date_in_future` between 00:00 and 02:00 German time.
  5. Store: `parse_date` or `null` (the `date_unparseable` flag stays), `normalize_currency` or `EUR` (the `currency_unknown` flag stays), money quantized to 0.01 half up, `qty` unrounded.
  6. Each item goes through `LookupCategorizer.categorize(description, table)` (`services/categorization.py`, [domain-logic](domain-logic.md#categorizepy-f07)) for `normalized_name`, `unit`, `category` and `category_source`. The model's `qty` wins over the normaliser's, and the normaliser's unit is kept only with its own qty. `assess` also gets the duplicate candidates.
- **Error mapping:** each `ExtractionError` subclass maps to its receipt code. Any other exception (a bug, `UnknownPromptVersion`, a missing image file) becomes `interrupted` via `ExtractionInterrupted`.
- **If writing the outcome fails** (a `StorageError`), it is logged and the task tries once more to set `failed`. A failure outcome keeps its code (e.g. `llm_timeout`). A success outcome becomes `interrupted`, because the result is lost. Either way the raw output and latency are dropped. If the second try fails too, the startup reset catches it.
- **Raw output:** stored redacted for success, `malformed_output` (the last attempt only) and `not_a_receipt`; `null` for the `llm_*` codes, `unreadable_image` and `interrupted`. It is parsed with the extractor's own loader, `ai.extractor.load_json` (fence stripping, then the `{…}` slice). If that gives an object, its `merchant` is replaced by the cleaned value and it is re-serialised, so prose around the JSON is dropped. Otherwise it only gets `redact_text`. `latency_ms` is kept for every outcome that has one.
  - Before the review fix, the pipeline only tried fenced or bare JSON. So a prose-wrapped answer, which the extractor accepts, kept the merchant's address in the stored raw output. Any parser that judges model output must use the extractor's loader.
- **Retry,** and manual entry for a failed receipt, clear `error`, `model_name`, `prompt_version`, `latency_ms` and `raw_model_output`.
- **Logging:** fixed templates only (ids, codes, counts, exception type names), listed in `ALLOWED_TEMPLATES` in `tests/api/test_extraction_logs.py` and checked by an AST test over `app/services/`. Never `logger.exception` in services, because a traceback can quote values. An API test at DEBUG level checks that no distinctive value of any recorded case appears in any log record.
  - A log-privacy test must set DEBUG on the `app` logger too (`caplog.set_level(DEBUG, logger="app")`). `create_app` sets `app` to INFO, so setting only the root logger captures no DEBUG records from `app.*`, and the test passes without checking anything. The F03 extractor test had this flaw until F05.
  - `api/errors.py` still logs a `StorageError` with its traceback. The engine is built with `hide_parameters=True`, so SQL parameters (merchants, descriptions) never appear in it. A test forces a real failed insert and checks the log.

- **Per-version output spec:** `ai/schema.py` maps each prompt version to an `OutputSpec(model, response_format, example_json)` in `OUTPUT_SPECS`; the extractor takes `{example}`, the `response_format` and the validation model from it.
  - **v1 is frozen** for F11: `ReceiptExtractionV1`, `LineItemV1`, `RESPONSE_FORMAT_V1` and `EXAMPLE_JSON_V1` are the pre-0019 schema and example, pinned by golden and SHA-256 tests against `a4a32e8`. Don't change them.
  - A valid v1 answer is converted to the v2 `ReceiptExtraction` (`model_validate(v1.model_dump())`), so the pipeline and API stay v2-shaped; the stored raw output stays v1-shaped, so F11 can score v1's subtotal, tax and unit price. A v1 answer that can't be converted (more than 6 `unreadable_fields` after dropping subtotal/tax) is invalid output: one repair, then `malformed_output`.
  - A v2-shaped answer under v1 lacks required keys: one repair, then `malformed_output`.
  - A prompt folder without a spec raises `MissingOutputSpec` (an `UnknownPromptVersion`) when the extractor is built, so the receipt fails with `interrupted`; a test requires a spec for every folder.
- **Since 0019** the pipeline reads only `total` and the item amounts from the model; `subtotal`, `tax` and `unit_price` are stored as `null` for AI expenses, and only those amounts go through the out-of-range check.

## Failure handling (criterion 10)
| Condition | Behaviour |
|---|---|
| Model server unreachable | receipt `failed` with `error=llm_unavailable`; `/health` shows `llm: down`; the rest of the app keeps working |
| Timeout | one retry, then `failed` with `error=llm_timeout` |
| Model server answers with an error | `failed` with `error=llm_error` |
| Malformed or unexpected output | one repair attempt, then `failed` with `error=malformed_output`, the last attempt's raw output kept (redacted); an amount beyond the `Money` range counts as this too |
| Output cut off at `LLM_MAX_TOKENS` | no repair (it would be cut off again), `failed` with `error=malformed_output`, raw output kept |
| Not a receipt | `is_receipt=false` or the plausibility rule: `failed` with `error=not_a_receipt` ([0015](../decisions/0015-non-receipt-is-a-failure-with-retry-or-manual-entry.md)) |
| Input can't be processed | wrong type or too large: `422 unsupported_file` / `413 file_too_large` at upload; the model server can't decode it: `failed` with `error=unreadable_image` |
| DB read/write error | `500 storage_error`, logged; `/health` shows `db: error` |
| Unexpected error in the extraction task | `failed` with `error=interrupted`, logged by exception type only |

F05 has one API test per row.

Every failed receipt offers **Enter manually**, and all but `unreadable_image` also offer **Retry** ([error-format](../contracts/error-format.md#receipt-error-codes)).

## Tests
- Recorded responses in `tests/fixtures/recorded_responses/`: valid, malformed then repaired, malformed twice, cut off at the token limit, missing fields, `is_receipt=false`, `is_receipt=true` on a non-receipt (from the spike), an injection attempt.
- `scripts/make_fixtures.py` generates them ([0017](../decisions/0017-personal-data-is-redacted-by-code.md)).
  - It reads only strict-schema spike outputs, redacted; the other cases are synthetic.
  - Rerunning it gives identical files.
  - An independent test scans every fixture for personal data.
- Logs never contain raw model output, prompts or image bytes. Log messages from `app.ai` are limited to a fixed set of templates (`ALLOWED_TEMPLATES` in `tests/unit/test_extractor.py`, which also scans the code). The pipeline stores the raw output redacted.
- A fake client covers timeouts and connection errors.
- An optional `@pytest.mark.integration` test runs against a live Ollama.
