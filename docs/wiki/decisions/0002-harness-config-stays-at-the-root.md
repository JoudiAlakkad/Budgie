# 0002 — Harness config stays at the root

**Status:** Accepted (2026-09-28)

## Context
The agent harness (Claude Code) is configured through `.claude/` (settings, agents, skills), `.devcontainer/` (the sandbox) and `CLAUDE.md`. Copying these per stack would give several permission policies that drift apart.

## Decision
- `.claude/`, `.devcontainer/` and the root `CLAUDE.md` live only at the repo root.
- Stack folders hold only a short `CLAUDE.md` with conventions ([0001](0001-monolith-layout-with-per-stack-agent-context.md)), never permissions or agent definitions.

## Consequences
- There is one sandbox and one permission policy to document and defend ([0011](0011-sandbox-hardening.md)).
- Custom subagents are defined in `.claude/agents/` even though each one works on a single stack ([0003](0003-custom-subagents-per-stack.md)).
