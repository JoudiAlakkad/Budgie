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
