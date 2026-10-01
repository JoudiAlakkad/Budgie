#!/usr/bin/env bash
# Create the roadmap milestones, labels and issues on GitHub.
# Run on the HOST (the dev container has no credentials, see decision 0011):
#   gh auth login
#   scripts/create-github-issues.sh [--dry-run]
# Safe to re-run: existing milestones, labels and issue titles are skipped.
set -euo pipefail

REPO="JoudiAlakkad/Budgie"
ISSUES_DIR="$(cd "$(dirname "$0")/.." && pwd)/.github/issues"
DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

run() {
  if $DRY_RUN; then printf '+'; printf ' %q' "$@"; echo; else "$@"; fi
}

MILESTONES=(
  "M1 Foundations"
  "M2 AI spike"
  "M3 Extraction pipeline"
  "M4 Domain logic"
  "M5 API + UI"
  "M6 Evaluation"
  "M7 Docs and hardening"
  "M8 Buffer and slides"
)

LABELS=(
  "backend:1d76db"
  "frontend:0e8a16"
  "contracts:5319e7"
  "ai:d93f0b"
  "docs:c5def5"
  "eval:fbca04"
)

# Creation order, so issue numbers follow the roadmap.
ISSUES=(F00 F01 spike F02 F03 F04 F05 F06 F07 F08 F09 F10 F11 F12)

if $DRY_RUN; then
  existing_milestones=""; existing_labels=""; existing_issues=""
else
  existing_milestones="$(gh api "repos/$REPO/milestones?state=all&per_page=100" --jq '.[].title')"
  existing_labels="$(gh label list --repo "$REPO" --limit 200 --json name --jq '.[].name')"
  existing_issues="$(gh issue list --repo "$REPO" --state all --limit 500 --json title --jq '.[].title')"
fi

for m in "${MILESTONES[@]}"; do
  if grep -qxF "$m" <<<"$existing_milestones"; then echo "milestone exists: $m"; continue; fi
  run gh api "repos/$REPO/milestones" -f title="$m" --silent
done

for entry in "${LABELS[@]}"; do
  name="${entry%%:*}"; color="${entry##*:}"
  if grep -qxF "$name" <<<"$existing_labels"; then echo "label exists: $name"; continue; fi
  run gh label create "$name" --repo "$REPO" --color "$color"
done

body_file="$(mktemp)"
trap 'rm -f "$body_file"' EXIT

for id in "${ISSUES[@]}"; do
  file="$ISSUES_DIR/$id.md"
  title="$(sed -n 's/^title: //p' "$file" | head -1)"
  milestone="$(sed -n 's/^milestone: //p' "$file" | head -1)"
  labels="$(sed -n 's/^labels: //p' "$file" | head -1 | tr -d ' ')"
  sed '1,/^---$/d' "$file" >"$body_file"

  if grep -qxF "$title" <<<"$existing_issues"; then echo "issue exists: $title"; continue; fi
  run gh issue create --repo "$REPO" --title "$title" --body-file "$body_file" \
    --milestone "$milestone" --label "$labels"
done
