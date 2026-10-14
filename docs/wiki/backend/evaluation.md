# Evaluation (F11)

How the extraction is measured. The rules (what counts as correct, flagged and committed) are in [0028](../decisions/0028-evaluation-truth-and-scoring.md); the results and the student's conclusions are in `docs/evaluation.md`.

## Dataset (`data/eval/`, git-ignored)
- `cases/cNN.json`, one per case, `images/` with the photos.
- Fields: `id`, `image` (a bare file name in `images/`), `kind` (`receipt`, `injection`, `non_receipt`), `merchant`, `date` (ISO or JSON `null`), `currency`, `total`, `items` (`description`, `amount`), `expected_status` (`accepted`, `needs_review`, `not_a_receipt`), optional `injected` (the values the injected text asks for, e.g. `{"total": 0}`), `notes`.
- `python -m eval.run_eval check` validates every case and lists each problem as `cNN.field: reason`.

## Code (`eval/`, a top-level package)
| Module | Role |
|---|---|
| `dataset.py` | ground-truth loader and validation |
| `scoring.py` | per-field and per-item scoring, injection and copied-example checks, percentiles |
| `metrics.py` | per-case scores, per-configuration aggregates, comparisons, error categories |
| `images.py` | downsizing like the frontend |
| `cache.py` | the local cache and its fingerprint |
| `results.py` | committed results, through redaction and the fixture scan |
| `report.py` | markdown tables and the marker-block replacement |
| `run_eval.py` | CLI: `check`, `run`, `score` |

- `eval` imports `app.ai`, `app.config`, `app.domain` and `app.services`, and never imports `app.api` or `app.db` **directly** (import-linter, direct imports only: `app.services` itself imports `app.db`, but nothing in `eval` opens a database). `app` never imports `eval`.
- Pillow comes from the `eval` extra (`pip install -e "./backend[dev,eval]"`); the Docker image doesn't install it.
- Tests are in `tests/eval/` and use synthetic cases only, since `data/eval/` is in neither CI nor a worktree.

## Running it
- The model server must be reachable at `LLM_BASE_URL`, and every configured model must be pulled (`ollama pull qwen2.5vl:3b`). The runner checks both first.
- From the dev container the host's Ollama is only reachable at `http://host.docker.internal:11434`; the config default `localhost` fails there. Run with `LLM_BASE_URL=http://host.docker.internal:11434/v1`. The host serves with `OLLAMA_CONTEXT_LENGTH=8192 ollama serve`; `curl …/api/ps` shows the loaded model's `context_length` once a call has started.
- A full run takes about 2 h (120 calls). Start it detached (`nohup … > log &`) and watch the log; tool shells stop after 10 minutes. `--timeout 300` keeps slow gemma calls (p95 over 200 s) from becoming infrastructure errors; the timeout isn't in the fingerprint.
- `make eval-check`, then `make eval` (`run`, default: the four configurations of 0028), or `python -m eval.run_eval run --config gemma3:4b@v3 --cases c02,c05`.
- `run` writes each answer to `data/eval/cache/<config>/<case>-r<n>.json` right after its call, so a stopped run resumes where it stopped. Infrastructure errors aren't cached, so they are retried.
- `make eval-score` (`score`) recomputes everything from the cache without the model, writes `eval/results/` and rewrites the generated block of `docs/evaluation.md`.
- **Calls vs scoring:** `--config`, `--models`, `--prompts` and `--cases` limit only the model calls. Scoring, `summary.json` and the report always cover every case of the four default configurations plus any other requested one, read from the cache. So `make eval EVAL_ARGS="--config gemma3:4b@v1 --partial"` retries v1's missing case and keeps the other rows and comparisons (found by `/code-review`: a subset run used to rebuild the summary from that subset only).
- **Complete or partial:** a write is complete only when no case run is missing or stale. Otherwise it exits 1 and writes nothing, unless `--partial` is given; the summary then stores `partial_reasons`, which the report's banner shows. Other configurations' result files are deleted only on a complete write.
- `--force` deletes a case's cache entry before the call, so a failed retry leaves it missing rather than scoring the old answer. Unknown prompt versions and two configurations with the same cache id exit 2 before any call.
- Exit codes: 0 done; 1 incomplete scope or cache entries missing or stale (nothing is written without `--partial`); 2 invalid input (ground truth, report markers, arguments, Pillow missing); 3 model server unreachable or a model not pulled.
- The report is re-read just before the block is replaced and written atomically, so edits made in `docs/evaluation.md` during an hour-long run survive (review fix).

## Cache fingerprint
- It covers the model, the prompt version, the system prompt as sent, the user and repair prompts, the response format, temperature, `max_tokens`, the image's SHA-256 and the run number (runs count from 1).
- It doesn't cover the MIME type or the extractor's parsing and repair code. After a change to `load_json`, `parse_output` or the repair trigger, rerun with `--force`.
- Stale entries count as missing in `score`. Infrastructure errors are never cached.
