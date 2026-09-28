# 0001 — Monolith layout with per-stack agent context

**Status:** Accepted (2026-09-28)

## Context
Budgie is one service built by one student in about 120 hours. It has a Python backend and a small browser UI. Coding agents work better when they load only the conventions for the stack they are touching.

## Decision
- One repository and one deployable service.
- Code is split into `backend/` (Python, FastAPI) and `frontend/` (static HTML/JS).
- Each stack folder has its own short `CLAUDE.md` with that stack's conventions. It is loaded when an agent works in that folder.
- The two stacks meet only at the API described in [contracts](../contracts/).

## Consequences
- Agents get small, relevant context, and backend and frontend work can run in parallel.
- A contract change must update both sides and the `contracts/` pages in the same commit.
- Related: [0002](0002-harness-config-stays-at-the-root.md), [0009](0009-contract-first-parallel-development.md).
