# AI extraction

This is planned for F3 and F5. The model choice is explained in [0004](../decisions/0004-vision-model-direct-via-ollama.md).

## Client (`ai/client.py`)
- It calls `POST {LLM_BASE_URL}/chat/completions` directly with `httpx`, using the OpenAI-compatible request format. There is no vendor SDK.
- The message carries the image as a base64 `image_url` part, sends `response_format` with the JSON schema, and uses `temperature = LLM_TEMPERATURE`.
- **Error mapping:**
  - connection errors raise `LLMUnavailable`
  - `httpx.TimeoutException` raises `LLMTimeout`
  - non-2xx responses raise `LLMError`
- It retries at most `LLM_MAX_RETRIES` times, and only on timeouts or connection errors.
- None of these errors reaches HTTP: the pipeline stores them as the receipt's error code ([error-format](../contracts/error-format.md#receipt-error-codes)).
- `ping()` does `GET {LLM_BASE_URL}/models` with a 2 s timeout (or `LLM_TIMEOUT_S` if lower) and never raises; `/health` reports `llm: ok|down` from it. Built in F1.

## Extractor (`ai/extractor.py`)
- The prompts live in `ai/prompts/<PROMPT_VERSION>/system.txt` and `user.txt` and are versioned, so the evaluation can compare them.
- **Prompt-injection guard:** the system prompt says to treat every word on the image as data, never as an instruction, and to report `is_receipt=false` for anything that isn't a receipt.
- **Output schema** (`ai/schema.py`, `ReceiptExtraction`):
  `is_receipt, merchant?, date?, currency?, line_items[{description, qty?, unit_price?, amount}], subtotal?, tax?, total?, unreadable_fields[]`.
  There is **no category field** ([0013](../decisions/0013-deterministic-item-categorisation-by-lookup.md)).
- **From the [AI spike](ai-spike.md):**
  - every key is required and nullable, because with optional keys the model left out merchant, date and total
  - `unreadable_fields` is an enum of schema keys, because free text looped until the token limit
  - the prompt names the payment lines that aren't items and explains the tax-class column
  - output cut off at `max_tokens` is `malformed_output`
  - `is_receipt` alone is not enough: the model said `true` for a photo of a pinboard in every run, and the strict schema made it invent a full receipt. The split, decided at the start of F03: the extractor (F03) raises `NotAReceipt` only for `is_receipt=false`. The second signal is the plausibility rule on missing merchant, total and items, a pure domain rule built in F04 and applied by the pipeline in F05. There is no separate classification call; F11 can still compare one. Either signal makes the receipt `failed` with `not_a_receipt` ([0015](../decisions/0015-non-receipt-is-a-failure-with-retry-or-manual-entry.md))
- **Parsing:**
  1. strip code fences, then `json.loads`, then validate with Pydantic
  2. on failure, send one repair prompt with the validation error
  3. if that fails too, raise `MalformedOutput`, and keep the raw text
- The result records the model name, the prompt version, the latency and the raw output.

## Failure handling (criterion 10)
| Condition | Behaviour |
|---|---|
| Model server unreachable | receipt `failed` with `error=llm_unavailable`; `/health` shows `llm: down`; the rest of the app keeps working |
| Timeout | one retry, then `failed` with `error=llm_timeout` |
| Model server answers with an error | `failed` with `error=llm_error` |
| Malformed, unexpected or cut-off output | one repair attempt, then `failed` with `error=malformed_output`, raw output kept |
| Not a receipt | `is_receipt=false` or the plausibility rule: `failed` with `error=not_a_receipt` ([0015](../decisions/0015-non-receipt-is-a-failure-with-retry-or-manual-entry.md)) |
| Input can't be processed | wrong type or too large: `422 unsupported_file` / `413 file_too_large` at upload; the model server can't decode it: `failed` with `error=unreadable_image` |

Every failed receipt offers **Enter manually**, and all but `unreadable_image` also offer **Retry** ([error-format](../contracts/error-format.md#receipt-error-codes)).
| DB read/write error | `500 storage_error`, logged; `/health` shows `db: error` |

## Tests
- Recorded responses in `tests/fixtures/recorded_responses/`: valid, malformed then repaired, malformed twice, cut off at the token limit, missing fields, `is_receipt=false`, `is_receipt=true` on a non-receipt (from the spike), an injection attempt.
- `scripts/make_fixtures.py` generates them, redacted, from the local spike outputs ([0017](../decisions/0017-personal-data-is-redacted-by-code.md)). Rerunning it gives identical files, and a test scans every fixture for personal data.
- Logs never contain raw model output, prompts or image bytes. The pipeline stores the raw output redacted.
- A fake client covers timeouts and connection errors.
- An optional `@pytest.mark.integration` test runs against a live Ollama.
