# 0004 — Vision model direct via Ollama

**Status:** Accepted (2026-09-28). Approach confirmed by the [AI spike](../backend/ai-spike.md) on 2026-10-03. `gemma3:4b` stays the default **provisionally**: it reads short receipts well, but invents dates and totals on long ones. The comparison with `qwen2.5vl:3b` moves to the F11 evaluation.

## Context
Receipts arrive as photos. The brief requires a locally run model behind an OpenAI-compatible API, with the model and server replaceable through configuration. The target hardware is a laptop without a GPU (8–16 GB RAM).

## Decision
- A multimodal model reads the receipt image directly and returns JSON that matches a schema. There is no separate OCR step.
- **Runner:** Ollama, which serves an OpenAI-compatible API at `/v1/chat/completions`.
- **Default model:** `gemma3:4b` (Q4, about 3.3 GB). It supports images, handles German and English, and runs on CPU.
- **Comparison model** for the evaluation: `qwen2.5vl:3b`, or Gemma 4 E4B if Ollama offers it.
- The app calls the API with plain `httpx`, not a vendor SDK. The URL, model and settings come from `LLM_*` env vars.
- The model only transcribes the receipt. It never assigns categories ([0013](0013-deterministic-item-categorisation-by-lookup.md)).

## Consequences
- The pipeline is simple and has one AI failure point, handled as described in [ai-extraction](../backend/ai-extraction.md).
- Latency on CPU is high, about 30–90 s per receipt, which led to [0007](0007-async-extraction-with-polling.md).
- Swapping the model or server is a config change only, and the model comparison in the evaluation shows that it works.
