---
name: dev-log
description: Record one AI development log episode in docs/ai-dev-log.md (task, context, agent contribution, tools/permissions, verification, verdict, observation). Use when the user asks to log, record or save a development episode.
argument-hint: "[short episode title]"
disable-model-invocation: true
---

# Record a dev-log episode

Append **one** episode to `docs/ai-dev-log.md`, using `template.md` in this folder. The title is `$ARGUMENTS`; if it's empty, ask the user for a short title.

## 1. Collect evidence (facts only, never invent)
- **This session:** the task the user gave the agent and the instructions or context provided. That includes the wiki pages, the CLAUDE.md files and the issue or acceptance criteria.
- **Agents used:** the main session and/or subagents (`backend-dev`, `frontend-dev`, `reviewer`, `eval-runner`, Plan, Explore). Get their tools from `.claude/agents/*.md` and the permission rules from `.claude/settings.json`. Note whether they ran in a worktree.
- **Git:**
  - Read the last episode's `Commits:` line in `docs/ai-dev-log.md` to find where it ended.
  - Run `git log --oneline <last>..HEAD` and `git diff --stat <last>..HEAD`. If there's no earlier episode, use the commits relevant to this task.
- **Verification that actually ran:** `make check`/pytest summary lines, reviewer findings, CI status if known, and manual checks the user mentioned.
- **The feature ID** (F-number) from the branch name or the task.

## 2. Draft all seven fields
1. Development task given to the agent
2. Relevant context and instructions
3. The agent's proposed contribution
4. Tools and permissions used by the agent
5. How the result was verified
6. What was accepted, modified or rejected, and why
7. One observed benefit, limitation or risk

For fields 1–5, use only the evidence. If something is unknown, write `unknown` rather than guessing.

## 3. Ask the user
Show the draft. Then ask the user to write or confirm fields **6** and **7** in their own words, since they have to defend them in the exam, and to choose the outcome: `success`, `partial` or `failed`. Don't write anything until the user confirms.

## 4. Write
- Append the entry to `docs/ai-dev-log.md` under the next number: `## Episode N — <title>`. Fill in the date (today), the feature ID, the commit range and the outcome.
- If the file doesn't exist, create it with the header from `template.md`.
- Don't commit. Tell the user the entry is ready to commit.

## 5. Check coverage
After writing, report:
- how many episodes exist, against the target of 5–8
- whether at least one is `failed` or `partial`; warn if none is, because the brief requires at least one unsuccessful or unsuitable agent contribution

## Rules
- No secrets, tokens, `.env` contents or full transcripts. Summarise instead, and keep quotes to one line.
- Only the main session writes entries. If you are a subagent, return the draft instead of writing it.
