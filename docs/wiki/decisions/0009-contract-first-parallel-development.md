# 0009 — Contract-first parallel development

**Status:** Accepted (2026-09-28)

## Context
Backend and frontend subagents work in separate git worktrees at the same time. If they each guess what the API looks like, the results won't fit together.

## Decision
- Feature F2 fixes the API contract first: the Pydantic schemas in `backend/app/api/schemas.py`, the error format, the receipt lifecycle and the [contracts](../contracts/) pages.
- Backend and frontend agents run in parallel only after the contract for their feature is fixed, and only when their files don't overlap.
- Any change to the contract is made by the main session, not by a subagent.

## Consequences
- Parallel work merges cleanly, and CI's OpenAPI drift check catches contract changes that weren't documented.
- Contract changes cost a little extra time.
