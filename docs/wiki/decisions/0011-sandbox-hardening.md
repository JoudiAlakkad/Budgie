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
- **Claude Code permissions** in `.claude/settings.json`:
  - `deny`: reading `.env*`, `~/.ssh/**` and `~/.gitconfig`
  - `ask`: `git push`, `git commit`, `rm`, `docker`, `curl` and `wget`
  - `allow`: tests, lint and read-only git commands
- **Git:** the agent commits locally, and the student pushes from the host.
- **Network (optional):** an allowlist firewall for PyPI, GitHub, the Ollama registry, the Anthropic API and `host.docker.internal:11434`.

## Consequences
- Pushing requires a host terminal, which is a deliberate step.
- Verify with: `ssh-add -l` fails in the container, `git config --global -l` shows no credential helper, and reading `.env` is refused.
- The README documents all of this.
