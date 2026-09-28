# Claude Code Instructions

## Knowledge Base
- `docs/wiki/` is the project knowledge vault: Obsidian-style, cross-linked Markdown. The entry point is `docs/wiki/README.md`.
- **Before any task**, read `docs/wiki/README.md`, then the relevant area (`contracts/`, `backend/`, `frontend/`), then the decisions that apply.
- Consult the wiki when answering questions about architecture, components or conventions.
- Update the affected articles when a change makes them stale, in the same commit.
- **Only the main session writes to the wiki.** Subagents read it freely and report their findings back, and the main session records them.
- A new architectural choice gets a new numbered decision in `docs/wiki/decisions/` and a line in the index.

## Workflow
- Features follow `docs/wiki/plan/workflow.md`: one issue, one branch `feat/F<NN>-<slug>`, one PR.
- Run `make check` before reporting work as done.
- Commit locally only. Never `git push`; the student pushes from the host.
- After a noteworthy feature, suggest `/dev-log <title>`.
