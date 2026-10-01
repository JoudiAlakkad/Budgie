# 0011 — Sandbox hardening

**Status:** Accepted (2026-09-28)

## Context
The brief requires the agent harness to run sandboxed: access only to the workspace, no exposed secrets or SSH keys, confirmation for destructive commands, and network restricted where practical. By default, VS Code Dev Containers forward the host SSH agent and git credentials into the container.

## Decision
- **Devcontainer**
  - Only `/workspace` is mounted, and the container runs as the non-root user `vscode`.
  - SSH-agent forwarding and the git credential helper are turned off. On 2026-09-28 both were found to be forwarded into the container: a `vscode-ssh-auth-*.sock` socket and a `credential.helper` that points to VS Code.
    - In the container, `.devcontainer/devcontainer.json` sets `remoteEnv.SSH_AUTH_SOCK=""`, and `postAttachCommand` unsets `credential.helper`.
    - On the host, the student must set these **VS Code user settings**, because they're read on the host and not from `devcontainer.json`: `"dev.containers.gitCredentialHelperConfigLocation": "none"` and `"dev.containers.copyGitConfig": false`.
  - Claude Code is installed inside the container by `postCreateCommand`, together with the `anthropic.claude-code` VS Code extension. This way the agent always runs inside the sandbox and never on the host. The installer is an unpinned `curl | bash` that runs once, when the container is created, so it falls outside the agent's permission rules below.
  - `.claude/settings.local.json` holds per-machine overrides and is gitignored.
- **Claude Code permissions** in `.claude/settings.json`:
  - `deny`: reading `.env*`, `~/.ssh/**` and `~/.gitconfig`
  - `ask`: `git push`, `git commit`, `rm`, `docker`, `curl` and `wget`
  - `allow`: tests, lint and read-only git commands
- **Git: the push model.** The student chose this on 2026-09-28.
  - The agent commits locally. Only the student pushes, from a host terminal. No personal credentials and no access tokens exist inside the container.
  - **Branch protection on `main`** is set up on the server (GitHub) when the remote is created:
    - changes arrive only through PRs
    - every CI check from [0010](0010-ci-gates-before-merge.md) must pass
    - force-pushes and deletion of `main` are blocked

    The server enforces these rules no matter who pushes.
  - **Why Git-side limits alone aren't enough:**
    - A local `pre-push` hook or `remote.pushurl = no_push` can be undone by the agent (`--no-verify`, one `git config` call), so they aren't a boundary.
    - A forwarded credential is the student's full identity. It reaches **all** of their repositories and every server the SSH key opens, and branch protection on Budgie limits none of that.
    - So credentials stay out of the container, and branch protection guards `main` on top of that.
  - **Rejected:** a fine-grained token for Budgie only inside the container, which would let the agent push feature branches. It would put a secret inside the sandbox for little gain.
- **Network (optional):** an allowlist firewall for PyPI, GitHub, the Ollama registry, the Anthropic API and `host.docker.internal:11434`.

## Consequences
- Pushing requires a host terminal, which is a deliberate step.
- Verify with: `ssh-add -l` fails in the container, `git config --global -l` shows no credential helper, and reading `.env` is refused.
- The README documents all of this.
