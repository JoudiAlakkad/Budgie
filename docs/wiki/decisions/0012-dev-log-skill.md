# 0012 — Dev-log skill

**Status:** Accepted (2026-09-28). Amended 2026-10-05: aligned with the brief's "AI development log" section; see below.

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

## Amendment (2026-10-05)
Checked against the brief's "AI development log" section. The first five episodes were 500–700 words each, and the "detected and corrected" part of an unsuccessful episode was spread over several fields.
- The template headings use the brief's wording for the seven points.
- Fields 1–5 together are at most about 170 words and "Detected and corrected" about 80; fields 6 and 7 are the student's and are never cut. An episode links the wiki instead of repeating it. The brief asks for a short log and no transcripts. Episodes 1–5 were shortened to this format on 2026-10-05, with fields 6 and 7 unchanged.
- A **Detected and corrected** section (what was wrong → what detected it → how it was corrected) is required for `partial` and `failed` episodes.
- The outcome is judged on the agent's contribution, with definitions for `success`, `partial` and `failed`. The main session's own plan or brief counts as an agent contribution.
- The coverage check also reports which kinds of work are covered and which episodes are too long. At 8 episodes, the skill asks whether to replace, merge or go over.
- Fields 6 and 7 of existing episodes are never rewritten without the student's confirmation.
