# 0030 — Default model `qwen2.5vl:3b`

**Status:** Accepted (2026-10-20, the student's decision). Revises the default model in [0004](0004-vision-model-direct-via-ollama.md); everything else in 0004 stays.

## Context
0004 made `gemma3:4b` the default only provisionally. The F11 evaluation ([0028](0028-evaluation-truth-and-scoring.md), `docs/evaluation.md`, run of 2026-10-14) compared it with `qwen2.5vl:3b`. Both used prompt v3, ran on 30 cases at temperature 0 and were scored after the app's post-extraction step:

| | `gemma3:4b` | `qwen2.5vl:3b` |
|---|---|---|
| Merchant / date / total | 79 % / 43 % / 71 % | 89 % / 82 % / 93 % |
| Item F1 | 0.47 | 0.89 |
| Valid JSON without repair | 97 % | 100 % |
| False rejections | 1/28 | 0/28 |
| Out-of-scope rejected | 2/2 | 2/2 |
| Injection followed | 0/5 | 2/5 (c03, c22) |
| Latency p50 / p95 | 40 s / 116 s | 51 s / 131 s |

The latency was measured on the student's Apple Silicon host ([ai-spike](../backend/ai-spike.md)), not on a CPU-only laptop.

## Decision
- The `LLM_MODEL` default becomes `qwen2.5vl:3b` (Ollama tag; Q4_K_M, about 3.2 GB download, 3.75 B parameters, image + text input). The Ollama library listed it as manifest `fb90415cde1e` on 2026-10-20; the copy the evaluation used wasn't checked by digest.
- `gemma3:4b` stays the fallback: set `LLM_MODEL=gemma3:4b`, no code change. Prompts v1–v3 and the eval keep both models.
- **Context length:** qwen keeps more image resolution, so the Ollama server must run with `OLLAMA_CONTEXT_LENGTH=8192`. At 4,096, every 2,000 px photo failed with HTTP 400. The compose `ollama` profile sets it, and the README documents it for a host install.
- **Licence:** the 3B is not under Apache 2.0, even though Ollama shows Apache 2.0.
  - Ollama's licence layer for `qwen2.5vl:3b` is Apache-2.0 (blob `832dd9e00a68`), but the same blob is used for `:7b` and `:72b`.
  - Upstream, the three sizes have different licences: the 3B is `qwen-research`, the 7B `apache-2.0`, the 72B `qwen`.
  - The Ollama file is family-wide and only matches the 7B. Converting the weights to GGUF doesn't change their licence, so the upstream **Qwen Research License** applies to the 3B: non-commercial use only, "research or evaluation purposes".
  - Budgie is a non-commercial study project, so this is acceptable. Commercial use would need `gemma3:4b` (Gemma Terms of Use) or a licence from Qwen.
  - Sources: [Ollama library](https://ollama.com/library/qwen2.5vl:3b), [3B LICENSE](https://huggingface.co/Qwen/Qwen2.5-VL-3B-Instruct/blob/main/LICENSE), [7B model card](https://huggingface.co/Qwen/Qwen2.5-VL-7B-Instruct), [72B model card](https://huggingface.co/Qwen/Qwen2.5-VL-72B-Instruct). Checked on 2026-10-20. This is not legal advice.

## Consequences
- **Prompt injection gets more weight.** qwen followed 2 of 5 injections, and gemma v3 followed none. Both injected receipts still ended `needs_review` as shipped, so the user saw the flag. The code-side checks and the user's confirmation remain the protection ([uncertainty](../backend/uncertainty.md)), not the model's obedience.
- **More false alarms:** qwen's precision for "flagged" is 0.52 against gemma's 0.85, because almost every correct receipt still has an `uncategorized_item`. Recall stays 1.0 for both.
- About 10 s more latency per receipt (p50), which the async extraction absorbs ([0007](0007-async-extraction-with-polling.md)).
- An Ollama server at the default 4,096 context fails on full-size photos with HTTP 400, which surfaces as a failed extraction. The README's setup step prevents this.
- Recorded test fixtures stay on `gemma3:4b`. They test the parser and the pipeline, not the model.
- A host test is required: `docker compose up` with `db: ok`, then one demo receipt uploaded with the new default. **Open** until the student records it here (date, model, outcome).
