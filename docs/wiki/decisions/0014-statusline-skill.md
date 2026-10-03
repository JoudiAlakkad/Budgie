# 0014 — Status line skill

**Status:** Accepted (2026-10-03)

## Context
Long sessions with subagents fill the context window, and it's easy to lose track of which branch the session is on, since every feature has its own branch ([workflow](../plan/workflow.md)). Claude Code can show a custom status line, but a hand-written setting per machine drifts and isn't documented.

## Decision
- A project skill `.claude/skills/statusline/` is invoked manually with `/statusline`.
- Its script `statusline.sh` prints `📁 <project folder> 🌿 <branch>` on line 1 and a context-window progress bar with the used percentage on line 2 (green < 50 %, yellow < 80 %, red otherwise).
- The skill writes `statusLine` into `.claude/settings.local.json` only. `/statusline remove` takes it out again.

## Consequences
- The script is versioned with the harness config at the root ([0002](0002-harness-config-stays-at-the-root.md)); each developer opts in.
- The shared `.claude/settings.json` and its permission policy stay unchanged ([0011](0011-sandbox-hardening.md)).
- The script depends on `jq`. It is present in the current container image but not pinned in `.devcontainer/`; the skill checks for it before installing.
