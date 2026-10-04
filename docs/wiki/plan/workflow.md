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
- **Run-time tests** guard against ReDoS (exponential regexes) with fixed-size hostile inputs. Bound the input (redaction: `MAX_TEXT_CHARS` = 65,536), build each hostile string at that size, run it once, and assert it finishes under `SLOW_S` = 2 s. Linear code stays 30× or more below the limit, so a slow CI runner can't break it, while exponential code passes it after a few dozen characters. In F3, a 0.5 s budget on an unbounded input failed on the shared CI runner (about 2.3× slower). The growth-ratio checks that replaced it were more machinery than the risk needed, because quadratic cost at 64 KB is harmless.
- **Python:** local tests run on the dev container's 3.14; CI and the image use 3.12 ([0005](../decisions/0005-fastapi-and-vanilla-js-single-container.md)).
- **Image `HEALTHCHECK`:** only checks that `/api/health` answers. Because health is always 200, a container with `db: error` still counts as healthy; the docker gate checks `db: ok` separately.
- **Versions:** ruff is pinned exactly in `backend/pyproject.toml`, and `.pre-commit-config.yaml` must use the same `rev`.

## Git conventions
- Commit messages are short and imperative, and name the feature, e.g. `F05: persist extraction failures`.
- Agent-authored commits carry a `Co-Authored-By` line for Claude.
- The agent commits locally, and only the student pushes, from the host ([0011](../decisions/0011-sandbox-hardening.md)).
- The remote host is GitHub (`JoudiAlakkad/Budgie`). It holds the issues, milestones, PRs and Actions.
- **When the remote is created**, protect `main`: require PRs, require all CI checks to pass, and block force-pushes and deletion ([0011](../decisions/0011-sandbox-hardening.md)).

## Dev log
Run `/dev-log <title>` after a noteworthy feature. See [0012](../decisions/0012-dev-log-skill.md) and `docs/ai-dev-log.md`. The target is 5–8 episodes, including at least one where the agent's work failed or was only partly used.
