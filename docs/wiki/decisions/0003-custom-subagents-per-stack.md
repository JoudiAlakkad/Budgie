# 0003 — Custom subagents per stack

**Status:** Accepted (2026-09-28)

## Context
Features are built by subagents ([workflow](../plan/workflow.md)). With built-in general-purpose agents, the stack rules and guardrails would have to be repeated in every prompt, and it would be easy to forget one.

## Decision
Four custom agents live in `.claude/agents/`:

| Agent | Scope | Purpose |
|---|---|---|
| `backend-dev` | `backend/`, `tests/` | implements backend features with tests |
| `frontend-dev` | `frontend/` | implements UI features against the contracts |
| `reviewer` | read-only | checks a diff against acceptance criteria, contracts and failure handling |
| `eval-runner` | `eval/results/` | runs the evaluation and summarises it |

The built-in Plan and Explore agents are still used for design and search.

## Consequences
- The guardrails are built into each agent: no wiki edits, no pushes, and a report back to the main session.
- Claude Code cannot restrict edits by path, so the scope is enforced by the agent prompt, the `reviewer` agent and CI.
