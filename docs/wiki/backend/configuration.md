# Configuration

All settings come from environment variables, read by `app/config.py`. This table is the reference list; there is no `.env.example`. A local `.env` is optional and git-ignored. Nothing is hard-coded: no credentials and no machine-specific paths (criterion 17).

| Variable | Default | Purpose |
|---|---|---|
| `LLM_BASE_URL` | `http://localhost:11434/v1` | OpenAI-compatible server. In Docker on a Mac or Windows host, use `http://host.docker.internal:11434/v1` |
| `LLM_MODEL` | `gemma3:4b` | model name/tag |
| `LLM_API_KEY` | `ollama` | dummy; sent as a Bearer token for servers that require one. A `SecretStr`, so it stays out of `repr()` and logs; only `services/dependencies.py` calls `.get_secret_value()` |
| `LLM_TIMEOUT_S` | `180` | per request (read timeout); the connect timeout is `min(10, LLM_TIMEOUT_S)`. Raised from 120 in F3 after the [AI spike](ai-spike.md) |
| `LLM_MAX_RETRIES` | `1` | on timeout or connection error only |
| `LLM_TEMPERATURE` | `0` | |
| `LLM_MAX_TOKENS` | `2048` | an 11-item receipt used 695 tokens in the spike; output that reaches the limit is `malformed_output` |
| `PROMPT_VERSION` | `v1` | folder under `ai/prompts/`; must be a plain folder name (`[A-Za-z0-9_.-]+`, no `..`), else `UnknownPromptVersion` on the first extraction, which fails that receipt with `interrupted` |
| `DATABASE_URL` | `sqlite:///./data/budgie.db` | |
| `UPLOAD_DIR` | `./data/uploads` | |
| `MAX_UPLOAD_MB` | `10` | |
| `FRONTEND_DIR` | `./frontend` | static UI served at `/`; `/app/frontend` in Docker. Empty means no frontend is served. Relative to the working directory, so `make run` starts from the repo root |
| `LOG_LEVEL` | `INFO` | |

**Swapping the model or server** is a config change only: point `LLM_BASE_URL` at llama.cpp server, LM Studio or vLLM, and set `LLM_MODEL`.
