# Workflow

How a feature from the [roadmap](roadmap.md) moves from issue to merge.

## Per feature
1. **Main session:**
   - writes the issue: goal, acceptance criteria, and the wiki pages the agent must read (always the relevant [contracts](../contracts/) pages)
   - creates the branch `feat/F<NN>-<slug>`
2. **Plan agent** (built-in, read-only): proposes the design and the list of files to change. The student approves it or changes it.
3. **Implementation:**
   - `backend-dev` and/or `frontend-dev` run with `isolation: "worktree"`.
   - Agents run in parallel only if their files don't overlap and the contract for the feature is already fixed ([0009](../decisions/0009-contract-first-parallel-development.md)).
   - The wiki must be committed before agents start, because a worktree only contains committed files.
   - The agent must check its worktree's base commit before starting. In F1 one worktree started from `main` instead of the feature branch, and the agent had to fast-forward it. Its commit then needs a cherry-pick, not a fast-forward merge.
   - In F05 the worktree started on an older commit (`d7dc706`) again. The brief named the expected base hash and said to `git reset --hard` to it if reachable, so the agent fixed it itself and the branch fast-forwarded cleanly. Always put the base hash in the brief. In F12 all four worktrees started on `1712cee`, the `main` tip, instead of the feature branch. Each agent reset itself to the named hash, so this is the expected case, not an exception.
   - The worktree shares `/workspace/.venv`, whose editable install points at the main checkout. Run checks in a worktree with `PYTHONPATH=<worktree>/backend make check VENV=/workspace/.venv`, or they test the main checkout's code.
4. **Verification:**
   - `make check` must pass (lint, tests, import boundaries, OpenAPI drift).
   - The `reviewer` agent checks the diff, and `/code-review` runs on it.
   - The student tries the slice by hand.
5. **Merge:**
   - The main session merges the worktree branch and updates the affected wiki pages in the same commit.
   - It runs `/dev-log` if the episode is noteworthy. Rejected or corrected agent output should always be logged.
   - The student pushes from the host and opens a PR. It merges when CI is green, and the issue is closed.

## Issues and milestones
- Every roadmap feature (plus the AI spike) has a draft in `.github/issues/<ID>.md`. The draft's header holds the title, milestone and labels, and the body holds the goal, dependencies, acceptance criteria and the wiki pages to read.
- The student creates them on GitHub from the host with `scripts/create-github-issues.sh` (`--dry-run` prints the `gh` calls). It is safe to re-run: existing milestones, labels and titles are skipped.
- The drafts carry the roadmap's summary criteria. In step 1, the main session sharpens the issue on GitHub before work starts, and the PR closes it with `Closes #<n>`.

## Subagent guardrails
These are part of every custom agent in `.claude/agents/`.
- Read `docs/wiki/README.md`, the stack's area pages, the contracts and the relevant decisions before changing anything.
- Stay inside your scope: `backend/` + `tests/`, or `frontend/`.
- Never edit `docs/wiki/`, `.claude/`, `.devcontainer/` or the contract schemas. If one of these needs to change, report the change you propose instead.
- Never push, and use no network except `pip install`.
- End with a report: changed files, `make check` output, and open questions. The main session records durable findings in the wiki.

## CI checks
See [0010](../decisions/0010-ci-gates-before-merge.md): ruff, pytest, OpenAPI drift, docker build + health, import-linter, gitleaks. `main` is protected.
- **CI:** GitHub Actions on `JoudiAlakkad/Budgie`, built in F1. Each gate is its own job (`lint`, `test`, `openapi`, `imports`, `docker`, `gitleaks`), so branch protection can require them by name.
- **Local:** `make check` runs lint, tests, import rules and the OpenAPI drift check. The dev container has neither docker nor gitleaks: gitleaks is skipped with a notice, and the docker gate runs on the host with `make docker-check`. CI uses the same script, `scripts/docker-health-check.sh`, which checks health (`db: ok`, `llm: down`), a non-root user and mode 700 on `/data`.
- **Frontend checks without a JS engine:** the container has no Node or browser, so `tests/unit/test_frontend_static.py` checks the JS as text: no `innerHTML`-style sinks, `fetch` only in `api.js`, `CATEGORIES` equals the `Category` Literal, and every `api.*` path literal (with `/api` added, `${…}` as a path parameter) exists in `docs/openapi.json` for that method. Whether the pages actually run is only checked by the student's manual run on the host (F06).
  - **`hidden` vs CSS `display` (F12 host test):** a class with `display: block` (`.flag-message`) beats the browser's own `[hidden] { display: none }`, so a note the JS hid still showed ("Choose a category before confirming" on every categorised item, unnoticed since F06). `style.css` now has a global `[hidden] { display: none !important; }`, and `test_frontend_static.py` checks for it.
  - **`aria-describedby` and `hidden` (F12, `/code-review`):** the accessible description includes a node that `aria-describedby` points to directly even when it is `hidden`. So the item category `select` lists its flag, missing-category note and error nodes only while each is shown; `review.js` recomputes the list at render and on `change`, and drops the attribute when it is empty. Since [0031](../decisions/0031-user-can-mark-unreadable-flags-checked.md) a scalar input's list is also recomputed when a flag is marked as checked or unmarked. Hidden children of a visible described node, such as `#delete-photo` in `#delete-text`, are left out by the browser and need nothing. Checked across all pages by `frontend-dev`.
- **Run-time tests** guard against ReDoS (exponential regexes) with fixed-size hostile inputs. Bound the input (redaction: `MAX_TEXT_CHARS` = 65,536), build each hostile string at that size, run it once, and assert it finishes under `SLOW_S` = 2 s. Linear code stays 30× or more below the limit, so a slow CI runner can't break it, while exponential code passes it after a few dozen characters. In F3, a 0.5 s budget on an unbounded input failed on the shared CI runner (about 2.3× slower). The growth-ratio checks that replaced it were more machinery than the risk needed, because quadratic cost at 64 KB is harmless.
- **Python:** local tests run on the dev container's 3.14; CI and the image use 3.12 ([0005](../decisions/0005-fastapi-and-vanilla-js-single-container.md)). 3.14 evaluates annotations lazily and 3.12 eagerly, so code can pass `make check` and still crash the image on import. In F06, `ExpenseService` has a method `list`, and a later `-> list[LineItemChange]` resolved to that method in 3.12 (`TypeError: 'function' object is not subscriptable` at `docker run`). ruff `A003` now catches that pattern; `make docker-check` on the host catches the rest before a push.
- **Compose (F12):** `docker-compose.yml` runs the `app` service on the named volume `budgie-data:/data`, with its port bound to `127.0.0.1` only because there is no login. Each app setting is passed through only when it is set in the shell or `.env`, so `config.py` keeps the defaults; the one compose default is the model URL, read from its own variable `BUDGIE_LLM_BASE_URL` (default `host.docker.internal`), so a host `LLM_BASE_URL` in `.env` for `make run` can't point the container at itself (second `/code-review`). The override `docker-compose.ollama.yml` adds `ollama/ollama:0.35.1` with `OLLAMA_CONTEXT_LENGTH=8192` ([0030](../decisions/0030-default-model-qwen2-5vl-3b.md)), points the app at it and raises `LLM_TIMEOUT_S` to 600 for the CPU. It replaced an `ollama` profile plus an `.env` line (`/code-review`: the profile alone silently kept the host URL, and `.env` is also read by `make run`). CI builds and checks the image only, not the compose file, so `docker compose up` is checked by hand on the host.
- **Image `HEALTHCHECK`:** only checks that `/api/health` answers. Because health is always 200, a container with `db: error` still counts as healthy; the docker gate checks `db: ok` separately.
- **gitleaks config:** `.gitleaks.toml` extends the default rules with one allowlist: a "secret" that is only a decimal number is ignored. In F11, `generic-api-key` fired on `"completion_tokens_mean": 441.8235294117647` in `eval/results/summary.json` (a key name with "token" and a high-entropy value). CI scans the whole history, so rounding the numbers couldn't fix the pushed commit; the allowlist does. Checked with the pinned 8.21.2: 1 leak without the file, none with it.
- **Versions:** ruff is pinned exactly in `backend/pyproject.toml`, and `.pre-commit-config.yaml` must use the same `rev`.
- Pillow is pinned exactly in the `eval` extra (`pillow==12.3.0`, F12): the demo PNGs are checked pixel for pixel, and glyph rendering depends on the FreeType bundled in each Pillow wheel. `test_installed_pillow_is_the_pinned_one` reads the pin from `pyproject.toml`. An exact pin needs a 12.3.0 wheel for every Python that runs `make install` (3.12 in CI, 3.14 in the dev container); a new Python release may need a Pillow upgrade plus a re-render.

## Git conventions
- Commit messages are short and imperative, and name the feature, e.g. `F05: persist extraction failures`.
- Agent-authored commits carry a `Co-Authored-By` line for Claude.
- The agent commits locally, and only the student pushes, from the host ([0011](../decisions/0011-sandbox-hardening.md)).
- **Create a feature branch without tracking:** `git switch -c feat/F<NN>-<slug> --no-track origin/main`. The first push is `git push -u origin feat/F<NN>-<slug>`. In F4 the branch was created with `git checkout -b … origin/main`, which made `origin/main` its upstream, and a plain `git push` from the host put the F4 commits straight onto `main` with no PR (`dcbce9c..d7dc706`, 2026-10-05). The commits were already reviewed and green, so `main` was kept as it was rather than rewritten.
- **Stage files by name, never `git add -A`.** In F08 a manual run with `UPLOAD_DIR=./data/demo-uploads` left a real receipt photo in a folder `.gitignore` didn't cover, and `git add -A` committed it into a wiki commit that was then pushed. `.gitignore` now covers `data/*uploads*/`; check `git status` before every commit.
- **Code and the wiki it makes stale go in one commit** (CLAUDE.md). Merge an agent's commit with `git cherry-pick --no-commit` (or `git merge --squash`), add the wiki changes, then commit once. Only the contract-first wiki commit before agents start stands alone. In F08 the wiki followed in separate commits (found by `/code-review`).
- The remote host is GitHub (`JoudiAlakkad/Budgie`). It holds the issues, milestones, PRs and Actions.
- **When the remote is created**, protect `main`: require PRs, require all CI checks to pass, block force-pushes and deletion, and don't allow admins to bypass the rules ([0011](../decisions/0011-sandbox-hardening.md)). The F4 push to `main` went through, so check that protection is on and applies to admins.

## Dev log
Run `/dev-log <title>` after a noteworthy feature. See [0012](../decisions/0012-dev-log-skill.md) and `docs/ai-dev-log.md`. The target is 5–8 episodes, including at least one where the agent's work failed or was only partly used.
