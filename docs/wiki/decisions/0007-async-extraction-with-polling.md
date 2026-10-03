# 0007 — Async extraction with polling

**Status:** Accepted (2026-10-03). The [AI spike](../backend/ai-spike.md) measured 28–76 s per receipt with the model loaded, +15 s cold, and about 100 s for a runaway output.

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
