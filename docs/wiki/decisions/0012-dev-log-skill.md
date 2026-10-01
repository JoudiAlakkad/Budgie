# 0012 — Dev-log skill

**Status:** Accepted (2026-09-28)

## Context
The brief requires an AI development log with 5–8 episodes. Each episode records the task, the context, the agent's contribution, the tools and permissions used, how the result was verified, what was accepted, modified or rejected, and one observation. At least one episode must be unsuccessful. Written from memory at the end, such entries become vague.

## Decision
- A project skill `.claude/skills/dev-log/` is invoked manually with `/dev-log <title>`.
- It collects evidence from the session and git, drafts the seven fields, asks the student to write the verdict and observation, and appends the entry to `docs/ai-dev-log.md`.
- It then reports how many episodes exist and warns if none is marked failed or partial.
- It doesn't commit and never writes without confirmation.

## Consequences
- Entries are based on evidence and written while fresh.
- The student still owns the judgement in each entry, which matters for the exam discussion.
- The step is suggested at the end of every feature ([workflow](../plan/workflow.md)).
