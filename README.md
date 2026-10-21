# Budgie

**Budgie turns receipt photos into structured, user-verified expense records and flags where spending leaks out of the user's budget.**

A local vision model reads the receipt. Deterministic code checks, categorises and analyses the result, and the user confirms every expense. There is one service and one container, with no login (a single local user) and no cloud AI.

Contents: [Problem](#problem-and-responsibility) · [Scenarios](#core-scenarios) · [Architecture](#architecture-and-data-flow) · [Persistence](#persistence) · [UI](#user-interface) · [API](#api-and-integration-points) · [Local model](#local-model-and-inference-server) · [Configuration](#configuration) · [Run](#build-test-and-start) · [Evaluation](#evaluation) · [Uncertainty](#uncertainty-handling) · [Responsible design](#responsible-design) · [Agent sandbox](#sandboxed-agent-setup) · [Limitations](#known-limitations)

---

## Problem and responsibility
People who keep to a budget or save toward a goal lose track of spending that only exists on paper receipts. Typing receipts in by hand takes too long, so recurring or growing spending ("budget leaks") goes unnoticed.

The responsibility split is:
- **AI's role (one essential step):** a locally run vision model transcribes merchant, date, line items and total from the photo. Without it, every receipt would have to be typed in.
- **Deterministic logic (most of the code):**
  - number and date parsing, redaction of personal data, validation and the review status
  - item categorisation by a lookup table that learns the user's choices, and duplicate detection
  - budgets, the savings goal, and potential-leak detection
  - the CSV export

  The model never assigns a category and never writes a leak text.

## Core scenarios
1. **Scan and correct a receipt** (the main vertical slice). The user uploads a photo and the model extracts the data. The review page shows the photo next to an editable form. AI values carry an "AI-generated" badge, and flagged fields and uncategorised items are highlighted with the reason. The user corrects them and confirms, and the expense is saved. A category the user picks is reused on the next receipt.
2. **Budgets and overspend.** The user sets monthly limits per category and a savings goal. The dashboard shows spending against each limit, a month-end projection and the goal progress.
3. **Discover a potential leak.** After a few months of data, Budgie flags patterns with a computed explanation, e.g. *"Eating out: you've used 89 % of your 50,00 € budget (44,50 €) by 14.10.2026 … At this pace it runs out around 16.10.2026"*, or *"Snacks and sweets: 38,00 € this month, +60 % vs. your median of 23,75 €"* ([rules](docs/wiki/decisions/0023-leak-rules-and-thresholds.md)).

**Reproducible demo data:**
- `data/demo/receipts/` holds three synthetic receipt photos for scenario 1, with fictional shops and no personal data. `make demo-receipts` re-renders them.
  - `demo-01-supermarket.png`: seed categories, a Pfand line and one unknown item (CHIPS PAPRIKA)
  - `demo-02-cafe.png` (Cafe Amsel, 10,90): all eating out, with one item to categorise (KAESEKUCHEN)
  - `demo-03-kiosk-total-mismatch.png`: no new item to categorise. CHIPS PAPRIKA gets the category you picked on demo-01, and the printed total (5,29) is 1,00 more than the items (4,29), so the review shows a sum flag. Upload the receipts in order and confirm demo-01 first.
- `make seed-demo` (or `python -m app.demo_seed` in the container) loads several months of confirmed expenses, budgets and a goal into an **empty** database. Scenarios 2 and 3 run on this data ([0022](docs/wiki/decisions/0022-demo-data-by-explicit-command.md)). Seed first, then upload the demo receipts.

## Architecture and data flow
```
┌──────────── Budgie container (port 8000 only) ─────────────────┐
│  Browser UI (frontend/ static HTML+JS)                          │
│        │  HTTP /api/* only                                      │
│        ▼                                                        │
│  api/        FastAPI routers + schemas.py (public DTOs)         │
│        ▼                                                        │
│  services/   use cases: receipt pipeline, corrections, insights │
│     │            │                         │                    │
│     ▼            ▼                         ▼                    │
│  domain/      db/ (repositories,        ai/ (httpx client,      │
│  pure rules   SQLAlchemy)               prompts, schema)        │
│               ▼                           │                     │
│   volume budgie-data:/data                │                     │
│   ├─ budgie.db (SQLite)                   │                     │
│   └─ uploads/ (receipt images)            │                     │
└───────────────────────────────────────────┼─────────────────────┘
                                            ▼  OpenAI-compatible /v1
                                     Ollama (host or own container)
External clients ──► REST API / CSV export only
```
- **Layers:** `api/` handles HTTP only. `services/` is the only layer that combines `domain/` (pure rules, no I/O), `db/` and `ai/`. `import-linter` enforces these boundaries in CI.
- **Data flow for scenario 1:**
  1. `POST /api/receipts` stores the image and returns `202`. A background task calls the model ([0007](docs/wiki/decisions/0007-async-extraction-with-polling.md)).
  2. The model's JSON is validated against a schema and repaired once if malformed.
  3. Code then redacts personal data, drops non-item lines, checks the sums and dates, categorises each item by lookup and sets the review status with flags.
  4. The expense, its items and the raw model output are saved.
  5. The UI polls `GET /api/receipts/{id}` and shows the review form. The user confirms with `POST /api/expenses/{id}/confirm`.
  6. Only confirmed expenses feed the budgets and leak detection.
- Full detail: [architecture](docs/wiki/architecture.md), [module list](docs/wiki/backend/modules.md), [receipt lifecycle](docs/wiki/contracts/receipt-lifecycle.md), [decisions](docs/wiki/README.md#decisions).

## Persistence
- **SQLite** behind a repository layer ([0006](docs/wiki/decisions/0006-sqlite-behind-repository-layer.md)): receipts (incl. the raw model output, model name, prompt version, latency), expenses, line items, the item-category lookup table, budgets and the goal. Receipt images are files in `UPLOAD_DIR`.
- In Docker both live on the volume `budgie-data` at `/data`. The directory has mode `700` and belongs to a non-root user.
- **Create, read, update and delete** all happen through the UI and the API:
  - an upload creates a receipt
  - corrections (`PATCH`) change an expense; editing a confirmed expense un-confirms it
  - a category choice updates the lookup table
  - the Expenses page deletes an expense together with its receipt and image
- The schema is created on startup, and the seed categories are synced without touching the user's own choices.
- Details: [persistence](docs/wiki/backend/persistence.md).

## User interface
Plain HTML and vanilla JS served by the same app at `http://localhost:8000/` ([pages](docs/wiki/frontend/pages.md)):

| Page | What it does |
|---|---|
| Upload (`/`) | drag and drop a photo (downsized to 2,000 px in the browser), the list of recent receipts, manual entry without a photo |
| Review | photo next to the form; "AI-generated" badges; flags with reasons; "unknown, please fill in" for missing fields; required category for unknown items; Save draft / Save & confirm; Retry or Enter manually for a failed receipt |
| Expenses | list, filter, delete, CSV download |
| Dashboard | spend vs. budget per category, month-end projection, savings goal, potential-leak cards with their explanation |
| Settings | monthly budgets and the savings goal |

Statuses are shown as words with an icon, never only as a colour, and never as a percentage.

## API and integration points
- **REST API under `/api`.** The OpenAPI document is served live at `http://localhost:8000/docs` (Swagger UI) and `/openapi.json`, and exported to [docs/openapi.json](docs/openapi.json). CI fails if the export drifts from the code.
- **Endpoints:** receipts (`POST/GET /receipts`, `GET /receipts/{id}`, `/image`, `POST /receipts/{id}/extract`, `DELETE`), expenses (CRUD, `/confirm`, `/export.csv`), item categories, budgets, goal, insights (`/summary`, `/leaks`), and `GET /health`. The full table with request and response DTOs is in [api-endpoints](docs/wiki/contracts/api-endpoints.md).
- **Errors** share one shape, `{"error": "<code>", "detail": "<text>", "fields": …}`, with documented 4xx/5xx codes ([error format](docs/wiki/contracts/error-format.md)).
- **Integration boundaries (criterion 8):** other components may use only
  - the REST API, which includes the images via `GET /api/receipts/{id}/image`
  - the documented [CSV export](docs/wiki/contracts/csv-export.md) (`GET /api/expenses/export.csv`; one row per item, with a `source` column that marks AI-extracted data)

  Nothing else reads the SQLite file, the uploads directory or internal classes. The Pydantic DTOs in `api/schemas.py` are the public contract.
- Quick check: `curl http://localhost:8000/api/health` returns `{"status":"ok","db":"ok","llm":"ok|down","model":"qwen2.5vl:3b"}`.

## Local model and inference server
| | |
|---|---|
| Inference server | **Ollama 0.35.1** (tested version; the image `ollama/ollama:0.35.1` in `docker-compose.ollama.yml`) |
| API used | OpenAI-compatible `POST {LLM_BASE_URL}/chat/completions` with an image, plus `GET /models` for health. Called through plain `httpx`, with no vendor SDK |
| Default model | **`qwen2.5vl:3b`** (Ollama tag; Qwen2.5-VL-3B-Instruct, 3.75 B parameters, Q4_K_M, 3.2 GB download) |
| Fallback model | `gemma3:4b` (`LLM_MODEL=gemma3:4b`) |
| Model parameters | temperature `0`, `max_tokens` 2048, timeout 180 s with one retry, JSON-schema `response_format`. The server needs a **context of 8,192** (`OLLAMA_CONTEXT_LENGTH=8192`) |
| Prompts | versioned text files in [backend/app/ai/prompts/](backend/app/ai/prompts/) (`v3/system.txt`, `user.txt`, `repair.txt`; v1 and v2 are frozen for the evaluation). Output schema: [backend/app/ai/schema.py](backend/app/ai/schema.py) |

**Why this model** ([0004](docs/wiki/decisions/0004-vision-model-direct-via-ollama.md), [0030](docs/wiki/decisions/0030-default-model-qwen2-5vl-3b.md)):
- **Modality:** it takes image input, so no separate OCR step is needed.
- **Quality:** in the evaluation it beat `gemma3:4b` on every field (total 93 % vs 71 %, item F1 0.89 vs 0.47).
- **Latency:** p50 51 s, a little slower than gemma; the async pipeline hides it.
- **Memory:** about 3.2 GB of weights.
- **Context:** it needs 8,192 tokens because it keeps more image resolution.
- **Server support:** it runs on Ollama.
- **Licence:** Ollama's page shows Apache-2.0 for `qwen2.5vl:3b`, but it shows that same licence file for the 7B and 72B too. Upstream, the 3B is under the **Qwen Research License**: non-commercial use only, "research or evaluation purposes". Converting the weights doesn't relicense them, so Budgie treats the 3B as research-only. That fits this study project. For commercial use, switch to `gemma3:4b` (Gemma Terms of Use). Sources, checked 2026-10-20, not legal advice: [Ollama library](https://ollama.com/library/qwen2.5vl:3b), [Qwen2.5-VL-3B LICENSE](https://huggingface.co/Qwen/Qwen2.5-VL-3B-Instruct/blob/main/LICENSE), [7B card (Apache-2.0)](https://huggingface.co/Qwen/Qwen2.5-VL-7B-Instruct), [72B card (Qwen licence)](https://huggingface.co/Qwen/Qwen2.5-VL-72B-Instruct).

**Hardware:**
- No GPU is required. Plan for at least **8 GB RAM** (16 GB is comfortable): the model takes about 3.2 GB on disk and **3.2 GB in memory** with the 8 K context (`ollama ps` on the student's Apple Silicon host, 2026-10-20: `SIZE 3.2 GB`, `100% GPU`, `CONTEXT 8192`). Add headroom for the OS, Docker and the browser.
- The measured latency (p50 about 51 s, p95 about 131 s per receipt) comes from an Apple Silicon Mac, where Ollama uses the GPU through Metal. A CPU-only laptop is slower, and so is Ollama inside Docker on a Mac, which has no GPU access.

**Install, start and verify the inference server (host install, recommended on a Mac):**
```bash
# 1. Install Ollama 0.35.1 from https://ollama.com/download (or: brew install ollama)
ollama --version                                   # expect 0.35.1 or later

# 2. Start it with the context qwen needs
#    Linux + Docker: Ollama listens on 127.0.0.1, which a container can't reach. Bind it to the
#    docker bridge only, e.g. OLLAMA_HOST=172.17.0.1 (never 0.0.0.0: Ollama has no login),
#    and then use that address instead of localhost below; or use docker-compose.ollama.yml.
OLLAMA_CONTEXT_LENGTH=8192 ollama serve
#    macOS app instead: launchctl setenv OLLAMA_CONTEXT_LENGTH 8192, then restart the app

# 3. Get the model (once, about 3.2 GB) and check it
ollama pull qwen2.5vl:3b
curl http://localhost:11434/v1/models              # lists qwen2.5vl:3b
ollama run qwen2.5vl:3b "Say OK"                   # loads the model once

# 4. With Budgie running:
curl http://localhost:8000/api/health              # "llm":"ok"
```
`llm: ok` only means the server answers. If the model isn't pulled, extraction fails with `llm_error` and the receipt can be retried. If Ollama runs at the default 4,096 context, full-size photos fail with HTTP 400.

**Replacing the model or the server** is a configuration change only. The model is reached through the OpenAI-compatible API, so nothing in the core logic changes:
- another Ollama model: `ollama pull <tag>`, then `LLM_MODEL=<tag>` (e.g. `gemma3:4b`)
- llama.cpp server, LM Studio or vLLM: set `LLM_BASE_URL` (under compose: `BUDGIE_LLM_BASE_URL`) to its `/v1` URL and `LLM_MODEL` to its model name. Set `LLM_API_KEY` if the server wants a bearer token.
- `GET /api/health` reports the configured model. The evaluation (`make eval EVAL_ARGS="--config <model>@v3"`) measures a new model on the same cases.

## Configuration
All settings are environment variables (pydantic-settings). Nothing is hard-coded: no credentials and no machine-specific paths. A local `.env` is optional and git-ignored. There are no secrets; `LLM_API_KEY` is a dummy value. The reference is [configuration](docs/wiki/backend/configuration.md).

| Variable | Default | Purpose |
|---|---|---|
| `LLM_BASE_URL` | `http://localhost:11434/v1` | OpenAI-compatible server. In Docker and in the dev container use `http://host.docker.internal:11434/v1`. Compose sets it from `BUDGIE_LLM_BASE_URL` (that default), never from `LLM_BASE_URL` |
| `LLM_MODEL` | `qwen2.5vl:3b` | model name/tag |
| `LLM_API_KEY` | `ollama` | dummy bearer token for servers that require one |
| `LLM_TIMEOUT_S` | `180` | read timeout per request |
| `LLM_MAX_RETRIES` | `1` | retries on timeout or connection error |
| `LLM_TEMPERATURE` | `0` | |
| `LLM_MAX_TOKENS` | `2048` | output that hits the limit counts as malformed |
| `PROMPT_VERSION` | `v3` | folder under `backend/app/ai/prompts/` |
| `DATABASE_URL` | `sqlite:///./data/budgie.db` (Docker: `sqlite:////data/budgie.db`) | |
| `UPLOAD_DIR` | `./data/uploads` (Docker: `/data/uploads`) | receipt images |
| `MAX_UPLOAD_MB` | `10` | |
| `FRONTEND_DIR` | `./frontend` (Docker: `/app/frontend`) | static UI; empty = API only |
| `LOG_LEVEL` | `INFO` | |

`OLLAMA_CONTEXT_LENGTH=8192` belongs to the Ollama server, not to Budgie. "Today" for the date rules is always the Europe/Berlin date.

## Build, test and start
**Requirements:** Docker with Compose, and an Ollama with `qwen2.5vl:3b` (see above). For development without Docker: Python 3.12+ and `make`.

**With Docker Compose (recommended):**
```bash
docker compose up -d --build                     # app on http://localhost:8000 (bound to 127.0.0.1), uses the host's Ollama
docker compose exec app python -m app.demo_seed  # optional: demo history for scenarios 2–3 (empty DB only, so before any upload)
docker compose logs -f app                       # follow the logs; `docker compose down` stops it (data stays on the volume)
```
Ollama in a container instead of on the host: the override file [docker-compose.ollama.yml](docker-compose.ollama.yml) adds the `ollama` service and points the app at it. Name both files on every command:
```bash
docker compose -f docker-compose.yml -f docker-compose.ollama.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.ollama.yml exec ollama ollama pull qwen2.5vl:3b
```
Shorter on macOS/Linux: `export COMPOSE_FILE=docker-compose.yml:docker-compose.ollama.yml`, then plain `docker compose …`. On Windows the separator is `;` instead of `:`.
This Ollama runs on the CPU (Docker on a Mac has no GPU), so extraction is much slower; the override raises `LLM_TIMEOUT_S` to 600 s.

Compose passes the `LLM_*`, `PROMPT_VERSION`, `MAX_UPLOAD_MB` and `LOG_LEVEL` variables from the [configuration table](#configuration) through only when they are set in the shell or in `.env`; otherwise the app's default applies. `DATABASE_URL`, `UPLOAD_DIR` and `FRONTEND_DIR` are fixed by the image and not passed through. The model server's URL has its own name in compose, **`BUDGIE_LLM_BASE_URL`** (default `http://host.docker.internal:11434/v1`). That way an `LLM_BASE_URL=http://localhost:11434/v1` in `.env`, which is right for `make run`, never points the container at itself.
Compose names the data volume `budgie_budgie-data` (project `budgie`). The plain `docker run` below uses `budgie-data`, a different volume, so data doesn't carry over between the two. `BUDGIE_PORT` changes the host port (default 8000). If compose reports `bind: address already in use`, something else holds port 8000: find it with `lsof -nP -iTCP:8000 -sTCP:LISTEN` (often a VS Code port forward of `make run` in the dev container), or start with `BUDGIE_PORT=8001 docker compose up -d`.

The app also starts without any model server. `/api/health` then reports `llm: down`, uploads fail with `llm_unavailable` and offer Retry or manual entry, and everything else (manual expenses, budgets, dashboard, export) keeps working.

**With Docker only:** `docker build -t budgie . && docker run -p 127.0.0.1:8000:8000 --add-host=host.docker.internal:host-gateway -v budgie-data:/data -e LLM_BASE_URL=http://host.docker.internal:11434/v1 budgie`

**Local development:**
```bash
make install          # venv + backend with dev and eval extras
make run              # uvicorn --reload on :8000 (in the dev container set LLM_BASE_URL=http://host.docker.internal:11434/v1)
make seed-demo        # demo data into an empty DB
make check            # ruff, pytest, import-linter, OpenAPI drift (gitleaks if installed)
make docker-check     # on the host: build, run, check health, non-root user and /data mode 700
```
- **Tests:** about 3,700 pytest tests. They cover table-driven domain rules (validation, review status, normalisation, categorisation, duplicates, budgets, each leak rule), recorded model responses (valid, malformed and repaired, cut off, missing fields, not a receipt, injected text), and API tests with a fake model client for every failure case. A live-model test is marked `integration` and excluded by default.
- **CI** runs `lint`, `test`, `openapi`, `imports`, `docker` and `gitleaks` on every pull request and every push to `main`, and `main` is protected ([0010](docs/wiki/decisions/0010-ci-gates-before-merge.md)).

**Failure handling** ([details](docs/wiki/backend/ai-extraction.md#failure-handling-criterion-10)):

| Condition | Behaviour |
|---|---|
| Model server unavailable | receipt `failed` / `llm_unavailable`, `llm: down` in health, Retry offered, rest of the app works |
| Timeout | one retry, then `failed` / `llm_timeout` |
| Malformed or unexpected output | one repair turn, then `failed` / `malformed_output`, raw output kept (redacted) |
| Input can't be processed | `422 unsupported_file`, `413 file_too_large`; an undecodable image → `unreadable_image`; not a receipt → `not_a_receipt` with Retry or Enter manually |
| Database can't be read or written | `500 storage_error`, logged; `db: error` in health |

## Evaluation
- **Dataset:** 30 hand-labelled cases: 23 German receipts (3 of them without a printed merchant or date, so ambiguous), 5 receipts with injected instructions (unsafe), and 2 photos that aren't receipts (out of scope).
- **Configurations:** 4, compared at temperature 0 and scored after the app's own post-processing: `gemma3:4b` with prompts v1, v2, v3, and `qwen2.5vl:3b` with v3.
- **Report:** method, metrics, comparisons, error categories and the failure discussion are in **[docs/evaluation.md](docs/evaluation.md)**. Runner: `eval/`, `make eval`.

| Configuration | Merchant | Date | Total | Item F1 | Valid JSON | Out of scope rejected | Injection followed | Latency p50 |
|---|---|---|---|---|---|---|---|---|
| `gemma3:4b` v3 | 79 % | 43 % | 71 % | 0.47 | 97 % | 2/2 | 0/5 | 40 s |
| `qwen2.5vl:3b` v3 | 89 % | 82 % | 93 % | 0.89 | 100 % | 2/2 | 2/5 | 51 s |

The eval photos (mostly real receipts; the 5 injection cases are AI-generated) and labels stay **out of the repo** (`data/eval/` is git-ignored) for privacy. The repo has the runner, the scoring rules ([0028](docs/wiki/decisions/0028-evaluation-truth-and-scoring.md)), the aggregated results (`eval/results/`) and the report.

## Uncertainty handling
Budgie shows **no confidence score and no probability** (criterion 13). Neither model gives a per-field score through Ollama, and at temperature 0 repeated runs always agree, so their agreement measures nothing. Instead, it does this:
- **Rules check the output:**
  - the item sum against the total (tolerance 0,02)
  - the date: not in the future, not older than 2 years
  - required fields
  - a plausibility rule: no merchant, no total and at most one item means not a receipt
  - a suspect Pfand sign
- **Three statuses with thresholds:** `accepted`, `needs_review` or `rejected`. Every flag carries a reason, shown next to its field.
- **Explicit unknown and escalation:**
  - A field the model can't read stays `null` and shows "unknown, please fill in".
  - A non-receipt becomes a failure with Retry or Enter manually.
  - Nothing counts until the user confirms, and confirming is refused while an item is uncategorised.
- **Measured:** recall of "flagged" was 100 % for every configuration, so no wrong extraction was accepted. Precision was 52 % for qwen, which means many false alarms, mostly from uncategorised items.

Full explanation and numbers: **[docs/wiki/backend/uncertainty.md](docs/wiki/backend/uncertainty.md)**.

## Responsible design
Summary of **[docs/responsible-design.md](docs/responsible-design.md)** (criterion 19):
- **Privacy:** the model runs locally, so no receipt leaves the machine. Card numbers, addresses and other personal data are kept out by the schema and redacted by code. Data stays on a private volume.
- **AI results are identifiable:** the "AI-generated" badge on the review page, the `source` field (`ai`, `ai_corrected`, `manual`) in the API and the CSV, and the raw model output stored with each receipt.
- **Failure modes and limits:** a wrong but internally consistent extraction can pass the rules. Leaks are only "potential". There is no probability, and the user decides.
- **Security and misuse:** text on a receipt is treated as data, but the model can still follow an injected instruction (qwen 2/5). Code checks and the user's confirmation are the safeguard. Other measures: a CSV formula guard, upload limits, a non-root container, and no secrets in the repo.

## Sandboxed agent setup
Budgie was built with **Claude Code** (Anthropic; Claude Opus models) as the agent harness, run inside a VS Code **dev container** ([0011](docs/wiki/decisions/0011-sandbox-hardening.md)). The development history is in the [AI development log](docs/ai-dev-log.md), with 11 episodes, including failed and partly used contributions.

| Measure | How |
|---|---|
| File access | only the repo is mounted (`/workspace`); the container runs as the non-root user `vscode` |
| No credentials in the container | SSH-agent forwarding is turned off (`remoteEnv.SSH_AUTH_SOCK=""`), and `postAttachCommand` removes the injected git credential helper. On the host, set the VS Code user settings `"dev.containers.gitCredentialHelperConfigLocation": "none"` and `"dev.containers.copyGitConfig": false` |
| Agent permissions ([.claude/settings.json](.claude/settings.json)) | **deny:** the Read tool on `.env*`, `~/.ssh/**`, `~/.gitconfig`, `~/.git-credentials`; `sudo`; `git push --force`. Shell commands such as `cat .env` are **not** matched by these rules, so they rely on the absence of secrets in the container, not on the deny list. **ask (confirmation):** `git commit`, `git push`, `git reset`, `git rebase`, `rm`, `docker`, `curl`, `wget`, `pip install`. **allow:** tests, lint, `make check`, read-only git |
| Push model | the agent commits locally only; the student reviews and pushes from a host terminal. No git or GitHub credential or token exists in the container (only Claude Code's own login) |
| Branch protection | `main` on GitHub accepts changes only through PRs with all CI checks green; force-push and deletion are blocked |
| Secrets in history | gitleaks runs in CI over the whole history; no credentials are in prompts, code or config |
| Network | **not restricted by a firewall.** The optional allowlist from 0011 wasn't built. `curl`/`wget` need confirmation, and the `backend-dev` brief allows no network beyond `pip install` |

**How the agent is used** ([workflow](docs/wiki/plan/workflow.md)):
- Each feature has one issue, one branch and one PR.
- A Plan agent proposes the design, and the student approves it.
- Custom subagents (`backend-dev`, `frontend-dev`, `reviewer`, `eval-runner`, `tutor`; see `.claude/agents/`) work in git worktrees inside their own folders.
- `make check`, the `reviewer` agent and `/code-review` check every change before the student tries it by hand.
- The project knowledge lives in a wiki that agents read first ([docs/wiki/](docs/wiki/README.md)).

**To verify the sandbox in the container:** `ssh-add -l` fails, `git config --global -l` shows no `credential.helper`, and the agent's Read tool is refused on `.env`. That last rule covers only the Read tool; the real protection is that no `.env` with secrets exists in the container (Budgie needs none).

## Known limitations
- **Accuracy:** even the best model got 7 % of totals and 18 % of dates wrong in the evaluation, and a wrong extraction that is internally consistent passes the rules. The user's review is required, not optional.
- **Prompt injection:** `qwen2.5vl:3b` followed 2 of 5 injected receipts. Both were still flagged `needs_review`, but the model's own resistance is weak.
- **Many false alarms:** with the seed table alone, most receipts have an uncategorised item, so correct receipts get flagged too. This improves as the user's choices are saved.
- **Latency:** 40–130 s per receipt on a laptop-class machine; Ollama in Docker on a Mac is slower.
- **Scope:** German receipts and number formats only. The eval set had no handwritten or English receipts. One local user, no login. The printed date is taken as is; the demo receipts' dates (October 2026) get flagged after October 2028.
- **Leak rules:** only `over_budget`, `on_pace_to_overrun` and `spike` are built; `recurring` and `small_frequent` are future work. Every leak is a "potential" leak.
- **Licence:** the default model is research-only (see above).
- **Not checked automatically:** the frontend has no browser tests (the dev container has no JS engine), and the compose file isn't checked in CI.
- More: [roadmap → future work](docs/wiki/plan/roadmap.md#future-work).

## Repository map
| Path | Content |
|---|---|
| `backend/app/` | FastAPI app: `api/`, `services/`, `domain/`, `db/`, `ai/` |
| `frontend/` | static HTML/JS/CSS UI |
| `tests/` | unit and API tests, recorded model responses |
| `eval/` | evaluation runner, scoring and results |
| `data/demo/receipts/` | synthetic demo receipts |
| `docs/` | [slides (PDF)](docs/slides.pdf), [evaluation](docs/evaluation.md), [responsible design](docs/responsible-design.md), [architecture](docs/architecture.md), [OpenAPI](docs/openapi.json), [AI dev log](docs/ai-dev-log.md), [wiki](docs/wiki/README.md) |
| `Dockerfile`, `docker-compose.yml`, `docker-compose.ollama.yml` | container build and startup; the override adds a containerised Ollama |

**Presentation slides:** [Budgie deck](https://claude.ai/artifact/JfDmFVz9CDskhLPRX2NoEs). It covers the brief's presentation list in 14 slides. The submitted copy is **[docs/slides.pdf](docs/slides.pdf)**. The claude.ai deck is the editable source, private until shared; after a change to it, export the PDF again to the same path.
