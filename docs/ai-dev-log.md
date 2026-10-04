# AI development log

Representative episodes of developing Budgie with an AI agent harness (Claude Code, sandboxed in a devcontainer; see README and `docs/wiki/decisions/0011-sandbox-hardening.md`). Target: 5–8 episodes, at least one unsuccessful or unsuitable agent contribution. Entries are added with `/dev-log <title>`.

## Episode 1 — Project plan, wiki and agent sandbox setup
- **Date:** 2026-09-28 · **Feature:** F0 · **Commits:** `adbeb32..3a72c72` · **Outcome:** partial

**1. Task given to the agent**
Plan the Budgie project against project.pdf and my problem statement. Then implement F0: move the approved plan into `docs/wiki/`, and set up the per-stack CLAUDE.md files, custom subagents, the `/dev-log` skill, permission rules and devcontainer hardening.

**2. Context and instructions**
- project.pdf (minimum criteria 1–20) and my problem statement
- the root `CLAUDE.md`, which names `docs/wiki/` as the knowledge vault
- my wiki README text (index only; only the main session writes)
- my answers during planning: vision model direct, a laptop without a GPU, FastAPI with vanilla JS, custom subagents, and categorisation by lookup table where I choose the category when an item isn't found

**3. Agent's proposed contribution**
- A project plan, refined over several rounds of my questions.
- 45 files:
  - the wiki index, roadmap, workflow and architecture pages
  - 13 decision records
  - the contracts, backend and frontend pages, and a snapshot of the original plan
  - the root, backend and frontend `CLAUDE.md` files
  - 4 agents in `.claude/agents/`
  - the `dev-log` skill and its template
  - `.claude/settings.json`
  - `devcontainer.json` changes

**4. Tools and permissions used**
- Only the main session (Claude Code, Opus 5.5) ran. There were no subagents and no worktree.
- Planning ran in plan mode, which is read-only apart from the plan file.
- After I approved the plan, it used Read, Write and Edit, and Bash for read-only checks plus `git checkout -b` and `git commit` on the feature branch.
- It didn't push. It ran under the default session permissions, because the new `settings.json` only takes effect in a new session.

**5. Verification**
- **Link check:** a Python script over `docs/**/*.md` found one broken link, to `plan/original-plan.md`. That showed the first setup command had silently not run: no branch was created and no plan snapshot was copied. I re-ran it and the check then passed.
- **Sandbox check:** `echo $SSH_AUTH_SOCK`, `ssh-add -l` and `git config --global -l` showed that the host SSH-agent socket and VS Code's git credential helper were forwarded into the container.
- There were no code tests, because there's no code yet, and no CI yet.

**6. Accepted / modified / rejected**
- **Accepted:** the wiki structure, the decision records, the agents and the skill.
- **Modified:**
  - The agent first put the `dev.containers.*` settings into `devcontainer.json`. They have no effect there, because VS Code reads them on the host. Checking the real container state exposed this. It was corrected with `remoteEnv` plus `postAttachCommand`, and the host settings were moved into decision 0011 as a manual step.
  - Async extraction (0007) was included even though I hadn't chosen it, so it's now marked "Proposed".
- **Rejected:** the first categorisation design (keyword matching, fuzzy matching, merchant fallback and an AI category). I replaced it with: normalise, look up, and let me choose.

**7. Observation (benefit, limitation or risk)**
*Risk:* the agent wrote a sandbox config that looked secure but didn't work, and a setup command failed without any error. Both were caught only by checking the real state (the container environment and a link check), not by reading the agent's output.

## Episode 2 — F01 skeleton: interrupted agent, review catches startup crashes
- **Date:** 2026-10-02 · **Feature:** F01 · **Commits:** `e2aad6c..2789451` · **Outcome:** partial

**1. Task given to the agent**
Build the F01 service skeleton: settings read from env vars, `GET /api/health` returning `{status, db, llm, model}` and always 200, a SQLite DB session, a minimal model-server client with `ping()`, an OpenAPI export with a drift check, and tests. `backend-dev` was limited to `backend/` and `tests/`. The main session wrote the Makefile, Dockerfile, CI workflow and docker check script.

**2. Context and instructions**
- Issue draft `.github/issues/F01.md`, rewritten to match the health contract.
- Wiki: `backend/` (modules, configuration, persistence), `contracts/api-endpoints.md` and `error-format.md`, `architecture.md`, and decisions 0005, 0006, 0010 and 0011.
- `backend/CLAUDE.md`, plus the committed Makefile, Dockerfile and CI workflow as fixed interfaces.
- Conflicts found during planning:
  - The roadmap said health returns `{app, db, llm}`, but the contract says `{status, db, llm, model}`; the contract was followed.
  - The dev container has Python 3.14 but CI uses 3.12.
  - There's no docker or gitleaks in the sandbox.

**3. Agent's proposed contribution**
- **First `backend-dev` run:** it was interrupted from the user's side after writing the app code (config, db, ai, services, api, main, openapi_export), before any tests, the venv or a commit. The main session then wrote the tests itself, which went against the workflow.
- **Second `backend-dev` run:** it reviewed the code and tests and fixed two bugs. An empty `FRONTEND_DIR` would have served the working directory, `.env` included. A storage error at startup crashed the app, so health could never report `db: error`. It also relaxed two import-linter contracts to direct imports only, and committed `ff2c5b6`.
- **`reviewer` and `/code-review`:** 14 findings, ranked from should-fix down to nits. The two most serious:
  - A bad `DATABASE_URL` crashed startup and could make health return 500.
  - A failed upload-folder step skipped table creation while health still said `db: ok`.
- **Third `backend-dev` run:** it fixed seven findings. It made the engine build on first use, made both storage steps run independently with a status that health reads, tightened the import contracts and tested them with temporary forbidden imports, pinned dependency ranges, made the API key a `SecretStr`, and tested all 13 env overrides. Committed as `ebdc32d`, cherry-picked as `d13a37a`.

**4. Tools and permissions used**
- `backend-dev`: Read, Edit, Write, Bash, Grep, Glob, run in a git worktree (`isolation: "worktree"`).
- `reviewer`: Read, Grep, Glob, Bash, read-only.
- `/code-review`: forked skill.
- Main session: everything else, under `.claude/settings.json`.
  - `git commit` and `pip install` need confirmation.
  - Reading `.env*` is denied. That rule also blocked writing `.env.example`, and the file was dropped.
  - No push; there are no credentials in the container (0011).
- Network: PyPI only, at about 30 kB/s. One `make install` failed with "from versions: none" because a request timed out.
- One worktree started from `main` instead of the feature branch, so the agent fast-forwarded it and its commit was cherry-picked.

**5. Verification**
- `make check` in the main checkout: ruff clean, 67 tests passed, 99 % coverage, import-linter 4 kept and 0 broken, OpenAPI up to date. gitleaks was skipped (not installed).
- Manual run: `/api/health` returned `{"status":"ok","db":"ok","llm":"down","model":"gemma3:4b"}`, and `/docs` returned 200.
- Agents' probe tests: a temporary forbidden import broke each tightened contract as expected.
- A final `/code-review` found only that `make run` serves `frontend/CLAUDE.md` locally; this was already deferred.
- Not yet run: `make docker-check` (host only) and CI on GitHub (Python 3.12).

**6. Accepted / modified / rejected**
- Accepted: the agent's app code and both bug fixes, plus seven of the review findings.
- Modified: the main session wrote the first tests after the interruption, and moved implementation details out of decision 0010.
- Deferred to F02: the shared error format (#7) and hiding the raw `Database` object from routers (#9).
- Rejected: `.env.example`, because the configuration page already lists every variable.

**7. Observation (benefit, limitation or risk)**
*Limitation:* the agent's first version passed its own tests but crashed on a bad `DATABASE_URL`. It took a separate read-only review to find the failure paths: the tests only covered the cases the implementer had thought of.

## Episode 3 — Non-receipt photo misreported in AI spike
- **Date:** 2026-10-03 · **Feature:** M2 (AI spike) · **Commits:** `71f0b30..550911b` · **Outcome:** failed

**1. Task given to the agent**
In an earlier session, the agent ran the milestone-2 AI spike: 6 photos through `gemma3:4b`, in a base and a strict schema variant, to confirm or revise [0004](wiki/decisions/0004-vision-model-direct-via-ollama.md) and [0007](wiki/decisions/0007-async-extraction-with-polling.md). It recorded the results in the wiki (`71f0b30`, `9be03b6`). The exact instructions of that session are unknown. In a later session the user asked for the spike's status, then whether the model had detected that one image was not a receipt, then to fix the affected wiki pages.

**2. Context and instructions**
CLAUDE.md (read the wiki first, update stale articles in the same commit, `make check` before reporting done, commit locally only); `docs/wiki/backend/ai-spike.md`, `ai-extraction.md`, decisions 0004 and 0007, the roadmap's M2 entry; raw outputs in `data/spike/` and the source photos in `data/receipts/` (both git-ignored).

**3. Agent's proposed contribution**
The original write-up (`9be03b6`, co-authored by Claude) described the sixth photo as "one US shop receipt" and reported merchant, date and total as present in 6/6 strict runs. The photo is a pinboard (mandala, notes, shortcut card). The model returned `is_receipt: true` in all 3 runs, and in strict mode invented a receipt from "Red Rock Trading Post" for 25.00 USD. The write-up's description matches the invented output; how it came about is unknown. In the later session the agent first repeated the wrong description in a status summary. Asked directly, it checked `is_receipt` in every output, looked at the image, found the error, recounted the results table over the 5 real receipts, added a non-receipt section with two untested ideas for a second signal, and updated `ai-extraction.md` and 0004 (`550911b`). It also flagged an edit to `scripts/ai_spike.py` it had not made, and the same date 2023-10-26 in two strict outputs (not checked).

**4. Tools and permissions used**
Main session only, no subagents, no worktree. Bash (`jq` over the spike outputs, `git log/show/diff`, `make check`), Read (wiki pages and the photo), Edit (three wiki files). `git commit` is on the `ask` list in `.claude/settings.json`; the commit was made after the user said to commit everything on the spike branch. Nothing was pushed.

**5. Verification**
The corrected numbers were recounted from the `checks` and `extraction` fields of `data/spike/*.json`, and the photo was inspected directly. `make check` exited 0 (4 import contracts kept, `docs/openapi.json` up to date, gitleaks skipped locally); it does not cover wiki content. No reviewer subagent ran.

**6. Accepted / modified / rejected**
Modified: the original spike write-up was partly wrong (the photo description and the "6/6" row). The corrected version was accepted in `550911b`, and the edit to the spike script was kept as the user's own.

**7. Observation (benefit, limitation or risk)**
*Risk:* the agent didn't check whether the output of the model was correct, so an invented receipt went into the project as fact. The user asked the agent directly to check, since there was an image that is not a receipt, and only then did the agent correct it.

## Episode 4 — F02 contracts: two review rounds
- **Date:** 2026-10-03 · **Feature:** F02 · **Commits:** `b761583..e09acb2` · **Outcome:** partial

**1. Task given to the agent**
"Now lets move to f02." The issue `.github/issues/F02.md` asks for request and response DTOs for every endpoint, one shared error format, a generated `docs/openapi.json`, and contract pages marked final.

**2. Context and instructions**
CLAUDE.md and the workflow (`docs/wiki/plan/workflow.md`); all of `docs/wiki/contracts/`; decisions 0007, 0008, 0009, 0013; the spike results (`ai-spike.md`, `ai-extraction.md`). The user decided four points: a non-receipt is a failed receipt with Retry or Enter manually rather than a `rejected` review (the user's own rule, recorded as [0015](wiki/decisions/0015-non-receipt-is-a-failure-with-retry-or-manual-entry.md)); money is a JSON number; unbuilt endpoints are stub routes answering 501 ([0016](wiki/decisions/0016-api-representation-and-stub-convention.md)); `backend-dev` may edit `schemas.py` and `docs/openapi.json` for F02 only.

**3. Agent's proposed contribution**
- **Plan agent** (read-only): about 26 gaps in the draft contract (missing error codes, an undefined extract response, undefined DTOs, no money rule) and the missing `python-multipart` dependency, which was confirmed.
- **Main session:** rewrote the four contract pages, added decisions 0015 and 0016, aligned 8 other wiki pages and sharpened the issue (`b761583`).
- **`backend-dev`** (worktree): the shared error format, all DTOs, 22 stub routes and 4 test files (`0d8e298`, `0950f6b`; 178 tests). Its worktree again started from `main` instead of `b761583`; it noticed this itself and reset before starting. It reported 13 places where the wiki left room and it made its own choice.

**4. Tools and permissions used**
Plan (built-in, read-only). `backend-dev` (Read, Edit, Write, Bash, Grep, Glob) in a worktree with the F02 authorisation above, using its own venv in the scratchpad. `reviewer` (Read, Grep, Glob, Bash, read-only). `/code-review` (forked skill, medium effort). The main session edited the wiki, merged, fixed the two `/code-review` findings and ran `pip install -e backend[dev]` for `python-multipart`. `git commit` and `pip install` are on the `ask` list; nothing was pushed.

**5. Verification**
- `reviewer`: "merge after fixes"; every acceptance criterion met, 4 should-fix findings. The main one: with the frontend mounted, as in production, an unknown `/api` path gave 405 and a wrong method gave 404. The agent's tests only ran without the frontend. `backend-dev` fixed them (`219dffc`, 284 tests).
- `/code-review` then found 2 more: a wrong method on `/expenses/export.csv` gave 422 instead of 405, and malformed JSON reported a field called `"1"`. The main session fixed both (`e09acb2`).
- Final `make check`: 290 passed, 4 import contracts kept, `docs/openapi.json` up to date. The user's manual check and CI (including the drift check on Python 3.12) are unknown at the time of writing.

**6. Accepted / modified / rejected**
Modified: the Plan agent's design was accepted, except for the non-receipt path, which the user changed. `backend-dev`'s code was accepted after two rounds of fixes: one by the agent after the reviewer, one by the main session after `/code-review`. Its 13 choices were kept and recorded in the wiki.

**7. Observation (benefit, limitation or risk)**
*Limitation:* the implementing agent's own tests only covered the setup it had in mind (no frontend). The bugs that matter in production only came out through separate read-only reviews. A green test suite wasn't enough evidence on its own.
