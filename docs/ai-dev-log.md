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
