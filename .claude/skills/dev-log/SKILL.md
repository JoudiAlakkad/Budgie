---
name: dev-log
description: Record one AI development log episode in docs/ai-dev-log.md (task, context, agent contribution, tools/permissions, verification, verdict, observation, and how a failure was detected and corrected). Use when the user asks to log, record or save a development episode.
argument-hint: "[short episode title]"
disable-model-invocation: true
---

# Record a dev-log episode

Append **one** episode to `docs/ai-dev-log.md`, using `template.md` in this folder. The title is `$ARGUMENTS`; if it's empty, ask the user for a short title.

## What the brief requires (section "AI development log")
- A **short** log of about **five to eight representative** episodes. A full transcript is "neither required nor desirable".
- Each episode records exactly these seven points, under the template's headings:
  1. the development task given to the agent
  2. relevant context and instructions
  3. the agent's proposed contribution
  4. tools or permissions used by the agent
  5. how the result was verified
  6. what was accepted, modified or rejected
  7. one observed benefit, limitation or risk
- At least one episode documents an **unsuccessful or unsuitable agent contribution** and explains **how it was detected and corrected**.
- The student must be able to explain and defend every entry in the exam (criterion 20).

## 1. Collect evidence (facts only, never invent)
- **This session:** the task the user gave the agent and the instructions or context provided. That includes the wiki pages, the CLAUDE.md files and the issue or acceptance criteria.
- **Agents used:** the main session and/or subagents (`backend-dev`, `frontend-dev`, `reviewer`, `eval-runner`, Plan, Explore). Get their tools from `.claude/agents/*.md` and the permission rules from `.claude/settings.json`. Note whether they ran in a worktree.
- **Git:**
  - Read the last episode's `Commits:` line in `docs/ai-dev-log.md` to find where it ended.
  - Run `git log --oneline <last>..HEAD` and `git diff --stat <last>..HEAD`. If there's no earlier episode, or the range spans several features, use only the commits of this task.
- **Verification that actually ran:** `make check`/pytest summary lines, reviewer findings, CI status if known, and manual checks the user mentioned.
- **Unsuitable contributions:** any agent output that was wrong, rejected or had to be fixed, from any agent, including the main session's own plan or brief. For each one: what was wrong, what detected it (test, reviewer, `/code-review`, the user, another agent), and how it was corrected (commit).
- **The feature ID** (F-number) from the branch name or the task.

## 2. Draft
Fill every heading of the template. For fields 1–5, use only the evidence; write `unknown` rather than guessing.

**Keep it short:** fields 1–5 together at most about **170 words**, and "Detected and corrected" at most about **80**. Fields 6 and 7 are the student's; suggest about 120 words for both, but never cut them.
- One or two sentences per field, or at most 4 bullets.
- Name commits, files and test counts; don't retell the conversation. Quotes at most one line.
- Leave out what the wiki already records (rules, thresholds, design details). Link the wiki page instead.

**Outcome:** judged on the agent's contribution, not on the final state of the code.
- `success`: accepted as proposed, or with only cosmetic changes.
- `partial`: part of it was wrong or unsuitable and was corrected (a reviewer finding, a rejected design, wrong claims).
- `failed`: the contribution was unusable and was rejected or redone.

**"Detected and corrected":** required when the outcome is `partial` or `failed`; otherwise write `–`. One bullet per problem: *what was wrong → what detected it → how it was corrected*.

## 3. Ask the user
Show the draft and its word counts (fields 1–5, fields 6–7, Detected and corrected). Then ask the user:
- to write or confirm fields **6** and **7** in their own words, since they have to defend them in the exam. Field 7 is **one** observation, starting with *Benefit:*, *Limitation:* or *Risk:*.
- to choose the outcome, with the definitions above. If the evidence shows an unsuitable contribution but the user picks `success`, point this out once, then follow their choice.

Don't write anything until the user confirms.

## 4. Write
- Append the entry to `docs/ai-dev-log.md` under the next number: `## Episode N — <title>`. Fill in the date (today), the feature ID, the commit range and the outcome.
- If the file doesn't exist, create it with the header from `template.md`.
- Don't commit. Tell the user the entry is ready to commit.

## 5. Check coverage
After writing, report:
- **Count:** how many episodes exist, against the target of 5–8. If there are already 8 before writing, say so before step 4, and ask whether to replace a less representative episode, merge two, or go over 8 on purpose.
- **Unsuccessful:** whether at least one episode is `partial` or `failed` and has a "Detected and corrected" section. Warn if none has.
- **Representative:** which kinds of work the episodes cover (planning and wiki, backend, frontend, AI spike or prompt, review and fixes, evaluation). Name the kinds still missing.
- **Length:** any episode whose fields 1–5 exceed about 170 words or whose "Detected and corrected" exceeds about 80.

## Rules
- No secrets, tokens, `.env` contents or full transcripts. Summarise instead, and keep quotes to one line.
- Never rewrite fields 6 or 7 of an existing episode without the user's confirmation; they are the student's words.
- Only the main session writes entries. If you are a subagent, return the draft instead of writing it.
