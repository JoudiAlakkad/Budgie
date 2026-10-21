# Configuration

All settings come from environment variables, read by `app/config.py`. This table is the reference list; there is no `.env.example`. A local `.env` is optional and git-ignored. Nothing is hard-coded: no credentials and no machine-specific paths (criterion 17).

| Variable | Default | Purpose |
|---|---|---|
| `LLM_BASE_URL` | `http://localhost:11434/v1` | OpenAI-compatible server. Under `docker compose` it is set from `BUDGIE_LLM_BASE_URL` (default `http://host.docker.internal:11434/v1`), never from `LLM_BASE_URL`. In Docker on a Mac or Windows host, and for `make run` inside the dev container (where `localhost` is the container), use `http://host.docker.internal:11434/v1` |
| `LLM_MODEL` | `qwen2.5vl:3b` | model name/tag. Default since [0030](../decisions/0030-default-model-qwen2-5vl-3b.md); `gemma3:4b` is the fallback. qwen needs the Ollama server started with `OLLAMA_CONTEXT_LENGTH=8192` (a server setting, not read by Budgie); at 4,096 full-size photos fail with HTTP 400 |
| `LLM_API_KEY` | `ollama` | dummy; sent as a Bearer token for servers that require one. A `SecretStr`, so it stays out of `repr()` and logs; only `services/dependencies.py` calls `.get_secret_value()` |
| `LLM_TIMEOUT_S` | `180` | per request (read timeout); the connect timeout is `min(10, LLM_TIMEOUT_S)`. Raised from 120 in F3 after the [AI spike](ai-spike.md) |
| `LLM_MAX_RETRIES` | `1` | on timeout or connection error only |
| `LLM_TEMPERATURE` | `0` | |
| `LLM_MAX_TOKENS` | `2048` | an 11-item receipt used 695 tokens in the spike; output that reaches the limit is `malformed_output` |
| `PROMPT_VERSION` | `v3` (since [0026](../decisions/0026-prompt-v3-non-item-guard-and-pfand-sign.md); `v1` and `v2` are frozen for the F11 comparison, and `v1` describes the old schema) | folder under `ai/prompts/`; must be a plain folder name (`[A-Za-z0-9_.-]+`, no `..`), else `UnknownPromptVersion` on the first extraction, which fails that receipt with `interrupted` |
| `DATABASE_URL` | `sqlite:///./data/budgie.db` | |
| `UPLOAD_DIR` | `./data/uploads` | |
| `MAX_UPLOAD_MB` | `10` | |
| `FRONTEND_DIR` | `./frontend` | static UI served at `/`; `/app/frontend` in Docker. Empty means no frontend is served. Relative to the working directory, so `make run` starts from the repo root |
| `LOG_LEVEL` | `INFO` | |

**Not configurable:** "today" for the date rules is the Europe/Berlin calendar date, because Budgie targets German receipts. The image has no system time zones, so `tzdata` is a Python dependency.

**Swapping the model or server** is a config change only: point `LLM_BASE_URL` at llama.cpp server, LM Studio or vLLM, and set `LLM_MODEL`.
