---
name: statusline
description: Install the project status line (📁 project folder, 🌿 git branch, context-window progress bar with used percentage) into Claude Code settings. Use when the user asks to set up, install, reset or remove the status line.
argument-hint: "[remove]"
disable-model-invocation: true
---

# Install the status line

The status line shows two lines:

```
📁 <project folder> 🌿 <branch>
████████░░░░░░░░░░░░ 42%
```

Line 2 is the share of the context window in use: green below 50 %, yellow from 50 %, red from 80 %. The script is `statusline.sh` in this folder. It reads the status line JSON on stdin and needs `jq`; git is optional (it shows `no git` outside a repo, and the short commit hash on a detached HEAD).

## Steps
1. Check that `jq` exists (`command -v jq`). If it doesn't, stop and tell the user.
2. Make the script executable: `chmod +x <repo root>/.claude/skills/statusline/statusline.sh`.
3. Smoke-test it:
   `echo '{"workspace":{"project_dir":"<repo root>"},"context_window":{"used_percentage":42}}' | <repo root>/.claude/skills/statusline/statusline.sh`
   It must print two lines. Show the output to the user.
4. Write the setting to `.claude/settings.local.json` (personal, not committed). Merge it into the existing JSON and keep every other key:
   ```json
   "statusLine": {
     "type": "command",
     "command": "<absolute repo root>/.claude/skills/statusline/statusline.sh",
     "padding": 0
   }
   ```
   Use the absolute path, because the status line command doesn't always run from the repo root.
5. Tell the user the status line updates with the next message.

## `/statusline remove`
Delete the `statusLine` key from `.claude/settings.local.json` and leave everything else unchanged.

## Rules
- Never write the setting to `.claude/settings.json`. The status line is a personal preference, and that file holds the shared permission policy ([0011](../../../docs/wiki/decisions/0011-sandbox-hardening.md)).
- Don't edit `statusline.sh` while installing. Layout changes go in a separate, deliberate edit.
