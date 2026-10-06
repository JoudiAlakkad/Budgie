#!/usr/bin/env bash
# Build the image, start it without a model server, and check /api/health.
# Used by CI and by `make docker-check` on the host.
set -euo pipefail

IMAGE="budgie:check"
NAME="budgie-check"
PORT="${PORT:-8000}"

docker build -t "$IMAGE" .
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" -p "$PORT:8000" "$IMAGE" >/dev/null
trap 'docker logs "$NAME" || true; docker rm -f "$NAME" >/dev/null 2>&1 || true' EXIT

body=""
for _ in $(seq 1 30); do
  if body="$(curl -fsS "http://localhost:$PORT/api/health")"; then break; fi
  sleep 1
done

echo "health: $body"
[[ -n "$body" ]] || { echo "health endpoint did not answer"; exit 1; }
grep -q '"db":"ok"' <<<"$body" || { echo "expected db: ok"; exit 1; }
grep -q '"llm":"down"' <<<"$body" || { echo "expected llm: down (no model server in this check)"; exit 1; }

user="$(docker exec "$NAME" id -u)"
[[ "$user" != "0" ]] || { echo "container runs as root"; exit 1; }

mode="$(docker exec "$NAME" stat -c %a /data)"
[[ "$mode" == "700" ]] || { echo "/data has mode $mode, expected 700"; exit 1; }

# The prompts are package data; a non-editable install drops them without package-data.
docker exec "$NAME" python -c "from app.ai.prompts import load_prompts; load_prompts('v1'); load_prompts('v2')" \
  || { echo "prompts v1 or v2 missing from the image"; exit 1; }
echo "docker check passed"
