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
4. **Verification:**
   - `make check` must pass (lint, tests, import boundaries, OpenAPI drift).
   - The `reviewer` agent checks the diff, and `/code-review` runs on it.
   - The student tries the slice by hand.
5. **Merge:**
   - The main session merges the worktree branch and updates the affected wiki pages in the same commit.
   - It runs `/dev-log` if the episode is noteworthy. Rejected or corrected agent output should always be logged.
   - The student pushes from the host and opens a PR. It merges when CI is green, and the issue is closed.

## Subagent guardrails
These are part of every custom agent in `.claude/agents/`.
- Read `docs/wiki/README.md`, the stack's area pages, the contracts and the relevant decisions before changing anything.
- Stay inside your scope: `backend/` + `tests/`, or `frontend/`.
- Never edit `docs/wiki/`, `.claude/`, `.devcontainer/` or the contract schemas. If one of these needs to change, report the change you propose instead.
- Never push, and use no network except `pip install`.
- End with a report: changed files, `make check` output, and open questions. The main session records durable findings in the wiki.

## CI checks
See [0010](../decisions/0010-ci-gates-before-merge.md): ruff, pytest, OpenAPI drift, docker build + health, import-linter, gitleaks. `main` is protected.

## Git conventions
- Commit messages are short and imperative, and name the feature, e.g. `F05: persist extraction failures`.
- Agent-authored commits carry a `Co-Authored-By` line for Claude.
- The agent commits locally, and only the student pushes, from the host ([0011](../decisions/0011-sandbox-hardening.md)).
- The remote host is GitHub or Gitea (to be decided). Both support issues, milestones, PRs and Actions.

## Dev log
Run `/dev-log <title>` after a noteworthy feature. See [0012](../decisions/0012-dev-log-skill.md) and `docs/ai-dev-log.md`. The target is 5–8 episodes, including at least one where the agent's work failed or was only partly used.
