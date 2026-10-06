# AI development log

Representative episodes of developing Budgie with an AI agent harness (Claude Code, sandboxed in a devcontainer; see README and `docs/wiki/decisions/0011-sandbox-hardening.md`). Target: 5–8 short episodes, at least one unsuccessful or unsuitable agent contribution with how it was detected and corrected. Entries are added with `/dev-log <title>`.

## Episode 1 — Project plan, wiki and agent sandbox setup
- **Date:** 2026-09-28 · **Feature:** F0 · **Commits:** `adbeb32..3a72c72` · **Outcome:** partial

**1. Development task given to the agent**
Plan Budgie against project.pdf and my problem statement, then implement F0: the wiki, the per-stack CLAUDE.md files, custom subagents, the `/dev-log` skill, permission rules and devcontainer hardening.

**2. Relevant context and instructions**
project.pdf (criteria 1–20), my problem statement, the root `CLAUDE.md` and my wiki README text. My planning answers: vision model direct, a laptop without a GPU, FastAPI with vanilla JS, custom subagents, categorisation by lookup table with my choice when an item isn't found.

**3. Agent's proposed contribution**
A project plan, refined over several rounds of my questions, then 45 files: wiki pages and 13 decision records, three `CLAUDE.md` files, 4 agents, the `dev-log` skill, `.claude/settings.json` and `devcontainer.json` changes.

**4. Tools or permissions used by the agent**
Main session only (Claude Code, Opus 5.5), no subagents. Plan mode (read-only) for planning; then Read, Write, Edit, and Bash for checks, `git checkout -b` and `git commit`. No push.

**5. How the result was verified**
A link check over `docs/**/*.md`, and a check of the real container (`echo $SSH_AUTH_SOCK`, `ssh-add -l`, `git config --global -l`). No code or CI yet.

**6. What was accepted, modified or rejected**
- **Accepted:** the wiki structure, the decision records, the agents and the skill.
- **Modified:**
  - The agent first put the `dev.containers.*` settings into `devcontainer.json`. They have no effect there, because VS Code reads them on the host. Checking the real container state exposed this. It was corrected with `remoteEnv` plus `postAttachCommand`, and the host settings were moved into decision 0011 as a manual step.
  - Async extraction (0007) was included even though I hadn't chosen it, so it's now marked "Proposed".
- **Rejected:** the first categorisation design (keyword matching, fuzzy matching, merchant fallback and an AI category). I replaced it with: normalise, look up, and let me choose.

**7. Observed benefit, limitation or risk**
*Risk:* the agent wrote a sandbox config that looked secure but didn't work, and a setup command failed without any error. Both were caught only by checking the real state (the container environment and a link check), not by reading the agent's output.

**Detected and corrected**
- A setup command silently didn't run (no branch, no plan snapshot) → the link check found a broken link → re-run, check passed
- `dev.containers.*` settings placed in `devcontainer.json`, where they have no effect; the SSH agent and git credentials were still forwarded → checking the container state → `remoteEnv` + `postAttachCommand`, host settings as a manual step in 0011
- A keyword/fuzzy/AI categorisation design → my review → replaced by normalise, look up, user chooses (0013)

## Episode 2 — F01 skeleton: interrupted agent, review catches startup crashes
- **Date:** 2026-10-02 · **Feature:** F01 · **Commits:** `e2aad6c..2789451` · **Outcome:** partial

**1. Development task given to the agent**
Build the F01 skeleton: env settings, `GET /api/health` (`{status, db, llm, model}`, always 200), a SQLite session, a model client with `ping()`, an OpenAPI export with a drift check, and tests. `backend-dev` was limited to `backend/` and `tests/`.

**2. Relevant context and instructions**
Issue `.github/issues/F01.md`; the backend and contract wiki pages; decisions 0005, 0006, 0010, 0011; the committed Makefile, Dockerfile and CI as fixed interfaces. The roadmap's health shape conflicted with the contract; the contract won.

**3. Agent's proposed contribution**
Three `backend-dev` runs: the app code (interrupted before tests); a self-review that fixed two bugs (`ff2c5b6`); and fixes for seven review findings (`ebdc32d`, cherry-picked as `d13a37a`).

**4. Tools or permissions used by the agent**
`backend-dev` (Read, Edit, Write, Bash, Grep, Glob) in a worktree; `reviewer` (read-only); `/code-review`. `git commit` and `pip install` need confirmation; reading `.env*` is denied; network PyPI only; no push.

**5. How the result was verified**
`make check`: 67 passed, 99 % coverage, 4 import contracts kept, OpenAPI up to date. Manual `/api/health` and `/docs`. Probe imports broke each tightened contract as expected. `make docker-check` and CI not yet run.

**6. What was accepted, modified or rejected**
- Accepted: the agent's app code and both bug fixes, plus seven of the review findings.
- Modified: the main session wrote the first tests after the interruption, and moved implementation details out of decision 0010.
- Deferred to F02: the shared error format (#7) and hiding the raw `Database` object from routers (#9).
- Rejected: `.env.example`, because the configuration page already lists every variable.

**7. Observed benefit, limitation or risk**
*Limitation:* the agent's first version passed its own tests but crashed on a bad `DATABASE_URL`. It took a separate read-only review to find the failure paths: the tests only covered the cases the implementer had thought of.

**Detected and corrected**
- The first run was interrupted before tests → the main session wrote them itself, against the workflow
- An empty `FRONTEND_DIR` would have served the working directory, `.env` included → the agent's second run → fixed (`ff2c5b6`)
- A bad `DATABASE_URL` crashed startup, and a failed upload step left health saying `db: ok` → `reviewer` and `/code-review` (14 findings) → lazy engine, independent storage steps (`d13a37a`)

## Episode 3 — Non-receipt photo misreported in AI spike
- **Date:** 2026-10-03 · **Feature:** M2 (AI spike) · **Commits:** `71f0b30..550911b` · **Outcome:** failed

**1. Development task given to the agent**
Run the milestone-2 AI spike: 6 photos through `gemma3:4b` in a base and a strict schema variant, to confirm [0004](wiki/decisions/0004-vision-model-direct-via-ollama.md) and [0007](wiki/decisions/0007-async-extraction-with-polling.md), and record the results. The exact instructions of that session are unknown.

**2. Relevant context and instructions**
CLAUDE.md; `ai-spike.md`, `ai-extraction.md`, decisions 0004 and 0007; the raw outputs in `data/spike/` and the photos in `data/receipts/` (git-ignored).

**3. Agent's proposed contribution**
A write-up (`9be03b6`) that described the sixth photo, a pinboard, as "one US shop receipt" and counted merchant, date and total as present in 6/6 strict runs. The model had said `is_receipt: true` and invented a receipt. After my question the agent corrected the write-up (`550911b`).

**4. Tools or permissions used by the agent**
Main session only, no subagents. Bash (`jq`, `git`, `make check`), Read (wiki pages and the photo), Edit. `git commit` needs confirmation; no push.

**5. How the result was verified**
The corrected numbers were recounted from `data/spike/*.json`, and the photo was inspected. `make check` passed, but it doesn't cover wiki content. No reviewer ran.

**6. What was accepted, modified or rejected**
Modified: the original spike write-up was partly wrong (the photo description and the "6/6" row). The corrected version was accepted in `550911b`, and the edit to the spike script was kept as the user's own.

**7. Observed benefit, limitation or risk**
*Risk:* the agent didn't check whether the output of the model was correct, so an invented receipt went into the project as fact. The user asked the agent directly to check, since there was an image that is not a receipt, and only then did the agent correct it.

**Detected and corrected**
- An invented receipt recorded as fact, repeated later in a status summary → my direct question whether the model had detected the non-receipt → the agent checked `is_receipt` in every output and the image, recounted the table over the 5 real receipts, and fixed `ai-spike.md`, `ai-extraction.md` and 0004 (`550911b`)

## Episode 4 — F02 contracts: two review rounds
- **Date:** 2026-10-03 · **Feature:** F02 · **Commits:** `b761583..e09acb2` · **Outcome:** partial

**1. Development task given to the agent**
"Now lets move to f02": request and response DTOs for every endpoint, one shared error format, a generated `docs/openapi.json`, and final contract pages (`.github/issues/F02.md`).

**2. Relevant context and instructions**
CLAUDE.md and the workflow; `docs/wiki/contracts/`; decisions 0007, 0008, 0009, 0013; the spike results. My decisions: a non-receipt is a failed receipt ([0015](wiki/decisions/0015-non-receipt-is-a-failure-with-retry-or-manual-entry.md)), money is a JSON number, unbuilt endpoints are 501 stubs ([0016](wiki/decisions/0016-api-representation-and-stub-convention.md)).

**3. Agent's proposed contribution**
Plan agent: about 26 gaps in the draft contract. Main session: four rewritten contract pages, decisions 0015 and 0016 (`b761583`). `backend-dev`: the error format, all DTOs, 22 stub routes and tests (`0d8e298`, `0950f6b`), plus 13 choices where the wiki left room.

**4. Tools or permissions used by the agent**
Plan (read-only); `backend-dev` (Read, Edit, Write, Bash, Grep, Glob) in a worktree, allowed to edit `schemas.py` for F02 only; `reviewer` (read-only); `/code-review`. `git commit` and `pip install` need confirmation; no push.

**5. How the result was verified**
`reviewer` and `/code-review` (see below). Final `make check`: 290 passed, 4 import contracts kept, OpenAPI up to date. CI unknown at the time of writing.

**6. What was accepted, modified or rejected**
Modified: the Plan agent's design was accepted, except for the non-receipt path, which the user changed. `backend-dev`'s code was accepted after two rounds of fixes: one by the agent after the reviewer, one by the main session after `/code-review`. Its 13 choices were kept and recorded in the wiki.

**7. Observed benefit, limitation or risk**
*Limitation:* the implementing agent's own tests only covered the setup it had in mind (no frontend). The bugs that matter in production only came out through separate read-only reviews. A green test suite wasn't enough evidence on its own.

**Detected and corrected**
- With the frontend mounted, an unknown `/api` path gave 405 and a wrong method 404; the agent's tests ran without the frontend → `reviewer` → fixed by `backend-dev` (`219dffc`)
- A wrong method on `/expenses/export.csv` gave 422, and malformed JSON named a field `"1"` → `/code-review` → fixed by the main session (`e09acb2`)
- The worktree started from `main` → the agent noticed and reset before starting

## Episode 5 — F04: agent corrects a wrong brief
- **Date:** 2026-10-05 · **Feature:** F04 · **Commits:** `d26949b..c145ad6` · **Outcome:** partial

**1. Development task given to the agent**
"we want to start with feature 4": validation, a rule-based review status and the plausibility rule for non-receipts (`.github/issues/F04.md`).

**2. Relevant context and instructions**
The issue, [domain-logic](wiki/backend/domain-logic.md), decisions 0008 and 0015, and the recorded fixtures. The user decided: no tax check (German VAT is included), keep the number parser, and recompute flags after every edit. A check against criterion 13 added [uncertainty](wiki/backend/uncertainty.md).

**3. Agent's proposed contribution**
`backend-dev` wrote `facts.py`, `validation.py` and `confidence.py`, with table-driven tests (`c20324f`). It reported that three fixture expectations in the main session's brief were wrong, and tested what the data gives instead.

**4. Tools or permissions used by the agent**
`backend-dev` (Read, Edit, Write, Bash, Grep, Glob) in a worktree, limited to `backend/` and `tests/`; `reviewer` (read-only); `/code-review` twice. `git commit` and `git reset` need confirmation; nothing was pushed.

**5. How the result was verified**
- `reviewer` and `/code-review` (see below)
- final `make check`: 1443 passed, 4 import contracts kept, OpenAPI unchanged
- second `/code-review`: no findings

**6. What was accepted, modified or rejected**
Modified: the agent's code was accepted after two fix rounds. Its correction of the brief's fixture expectations was accepted, and the main session updated the wiki and the issue to match. The main session's own wiki text was wrong in two places (the pinboard "Known limit", and `missing_fields` as implausible) and was corrected.

**7. Observed benefit, limitation or risk**
*Risk:* the main session's brief contained wrong claims about the test data, written without checking the fixtures. An agent told to "make these assertions pass" could have fitted the tests to those claims. Here it checked the data and reported the mismatch, but only because the brief allowed it ("report it, don't force it").

**Detected and corrected**
- Wrong fixture claims in the main session's brief → the agent ran them against the data → wiki and issue fixed (`c145ad6`)
- Float-built amounts falsely flagged at the 0.02 boundary → `reviewer`, reproduced → rounding to cents (`5a7f80b`)
- `1e30` crashed the sum check → `/code-review` → wide decimal context (`a6dcf06`)
- Branch created tracking `origin/main`, so `git push` put F04 onto `main` without a PR → GitHub's "no commits" → kept; branches now use `--no-track` ([workflow](wiki/plan/workflow.md#git-conventions))

## Episode 6 — F05: review catches a merchant-address leak in the pipeline
- **Date:** 2026-10-06 · **Feature:** F05 · **Commits:** `5369759..b13c713` · **Outcome:** partial

**1. Development task given to the agent**
"Plan feature f05", then "go ahead" and "start it": the receipt pipeline, covering upload, background extraction, persistence and failure handling (`.github/issues/F05.md`).

**2. Relevant context and instructions**
The Plan agent's design. The user made four decisions on it: `POST /expenses` moves to F05, the cleaned merchant also goes into the stored raw output, `interrupted` covers unexpected task errors, and a lock lets only one extraction run at a time. They were recorded in the wiki and in amendments to 0007 and 0017 before implementation (`5369759`).

**3. Agent's proposed contribution**
`backend-dev` built the tables, repositories, pipeline and receipt and expense routes in five commits (`6e76078..9aefa38`, 33 files, about 4,100 lines). It reported six departures from the design, for example `AUTOINCREMENT` after it saw ids being reused.

**4. Tools or permissions used by the agent**
- Plan agent (read-only)
- `backend-dev` (Read, Edit, Write, Bash, Grep, Glob) in a worktree, limited to `backend/` and `tests/`
- `reviewer` (read-only) and `/code-review`
- `git commit` and `git reset` need confirmation; nothing was pushed

**5. How the result was verified**
- `make check`, re-run by the main session: 1667 passed, 4 import contracts kept, OpenAPI unchanged
- `reviewer`: 1 blocker, 5 should-fix, 5 nits; `/code-review`: 1 finding
- By hand through the API: the happy path and the `llm_unavailable` path both worked

**6. What was accepted, modified or rejected**
Modified. The design and the six departures were accepted. Findings 1–5, 7, 8 and 9 were fixed in eight commits. The thread-pool stall was not fixed and was kept as a documented known limit.

**7. Observed benefit, limitation or risk**
*Risk:* a privacy rule that looks implemented can be bypassed by a second code path. The pipeline and the extractor parsed JSON differently, and only a review that compared the two found it.

**Detected and corrected**
- Worktree started on `d7dc706` → agent's base check → reset to `5369759`
- Prose-wrapped JSON kept the merchant's address in raw output → `reviewer`, reproduced → shared loader (`3581a16`)
- Range check before rounding → `GET /receipts` gave 500 → `reviewer`, reproduced → check after rounding (`7df0692`)
- Storage-error logs could hold SQL parameters with personal data → `reviewer` → `hide_parameters` (`7551747`)
