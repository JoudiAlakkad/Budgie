#!/usr/bin/env bash
# Claude Code status line, installed by /statusline.
# Line 1: 📁 <project folder> 🌿 <branch>
# Line 2: <context window bar> <used %>
# Reads the status line JSON from stdin; needs jq, git is optional.

input=$(cat)

dir=$(jq -r '.workspace.project_dir // .workspace.current_dir // .cwd // empty' <<<"$input")
[ -z "$dir" ] && dir=$PWD
project=$(basename "$dir")

branch=$(git -C "$dir" --no-optional-locks branch --show-current 2>/dev/null)
[ -z "$branch" ] && branch=$(git -C "$dir" --no-optional-locks rev-parse --short HEAD 2>/dev/null)
[ -z "$branch" ] && branch="no git"

# Prefer the precomputed percentage; otherwise derive it from the last turn's usage.
pct=$(jq -r '
  .context_window as $c
  | if $c.used_percentage != null then $c.used_percentage
    elif ($c.current_usage != null and ($c.context_window_size // 0) > 0) then
      (($c.current_usage.input_tokens // 0)
       + ($c.current_usage.cache_creation_input_tokens // 0)
       + ($c.current_usage.cache_read_input_tokens // 0)) * 100 / $c.context_window_size
    else 0 end
  | floor' <<<"$input" 2>/dev/null)
[[ "$pct" =~ ^[0-9]+$ ]] || pct=0
(( pct > 100 )) && pct=100

width=20
filled=$(( pct * width / 100 ))
bar=""
for ((i = 0; i < width; i++)); do
  if (( i < filled )); then bar+="█"; else bar+="░"; fi
done

if   (( pct >= 80 )); then color='\033[31m'   # red
elif (( pct >= 50 )); then color='\033[33m'   # yellow
else                       color='\033[32m'   # green
fi
reset='\033[0m'

printf '📁 %s 🌿 %s\n' "$project" "$branch"
printf "${color}%s${reset} %d%%\n" "$bar" "$pct"
