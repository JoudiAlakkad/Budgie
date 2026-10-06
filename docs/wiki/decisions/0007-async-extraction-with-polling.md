# 0007 — Async extraction with polling

**Status:** Accepted (2026-10-03), amended 2026-10-05 in F05. The [AI spike](../backend/ai-spike.md) measured 28–76 s per receipt with the model loaded, +15 s cold, and about 100 s for a runaway output.

## Context
A 4B vision model on CPU can take 30–90 s per receipt ([0004](0004-vision-model-direct-via-ollama.md)). Holding one HTTP request open that long risks timeouts in the browser or proxy, and the UI would look frozen.

## Decision
- `POST /api/receipts` stores the image, creates the receipt with status `uploaded`, and returns `202 Accepted` with its id.
- Extraction runs as a FastAPI background task: `extracting`, then `extracted` or `failed`.
- The UI polls `GET /api/receipts/{id}` until the status is final.

## Consequences
- Upload returns quickly, and the UI can show progress.
- Server-side timeouts (`LLM_TIMEOUT_S`) are separate from the HTTP request.
- A server restart during extraction leaves receipts stuck in `extracting`, so on startup those are reset to `failed` (retryable).
- The lifecycle is defined in [receipt-lifecycle](../contracts/receipt-lifecycle.md).

## Amendment (2026-10-05, F05)
- **One extraction at a time:** the task takes a module-level lock before `uploaded` → `extracting`, so `uploaded` means queued. The local model serves one request at a time anyway. A single-worker executor would hold no extra threads, but it was rejected because tests would need an inline runner. Each queued task holds a threadpool thread, which is acceptable for one user.
- **No DB session during the model call,** which can take up to about 12 min. Both status changes are guarded updates, so a receipt deleted during extraction just discards the result.
- **Unexpected errors** in the task (a bug, an unknown `PROMPT_VERSION`, a missing image) fail the receipt with `interrupted`. A new `internal_error` receipt code was rejected, because it would change the contract, and `interrupted` already offers the right actions.
- **Accepted limit, found in review:** a queued task waits for the lock on a thread from AnyIO's shared pool of 40, which every sync route also uses. With 40 or more receipts queued, the whole API, including polling and `/health`, hangs until the queue drains. The `reviewer` and `/code-review` both flagged it. The fix would be small: an async task that waits on a per-app `anyio.Lock` and runs only the extraction in a thread. The user chose to keep the limit, since one user rarely queues 40 receipts. Revisit it if a batch upload (F06) or the demo seed (F09) queues many receipts at once.
- **Single worker:** the lock and the startup reset assume one worker process ([ai-extraction](../backend/ai-extraction.md#pipeline-servicesreceipt_pipelinepy-f05)).
