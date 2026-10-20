# 0029 — Tutor agent for exam preparation

**Status:** Accepted (2026-10-15)

## Context
The student has to explain and defend every part of Budgie in the exam (criterion 20), including the parts an agent wrote. The answers live across the wiki, the decisions, the code, the evaluation and the dev log. The four agents of [0003](0003-custom-subagents-per-stack.md) build, review or run things; none of them is meant for questions.

## Decision
- A fifth custom agent, `tutor` (`.claude/agents/tutor.md`), answers questions about the whole project.
- **Read-only:** tools `Read`, `Grep` and `Glob`, with no Bash, so it can't run or change anything.
- **No personal data:** it never reads `data/` or `.env*`, and it answers evaluation questions from the committed, redacted results ([0017](0017-personal-data-is-redacted-by-code.md), [0028](0028-evaluation-truth-and-scoring.md)).
- **Answer shape:** a short answer, a step-by-step explanation with terms defined, the decision behind it, references as `path:line`, and optional exam questions.
- **Sources:** the wiki first, then the code, which wins when they disagree; it says "not documented" instead of guessing, and it keeps the student's decisions apart from the agent's work.

## Consequences
- The student can ask it directly ("ask the tutor agent why …") or the main session can delegate questions to it.
- Its answers are explanations, not records: like every subagent it never writes the wiki, so anything durable it finds goes through the main session.
- The path restriction on `data/` is enforced by the prompt only; Claude Code can't restrict reads by path for a subagent, which is the same limit as in 0003.
