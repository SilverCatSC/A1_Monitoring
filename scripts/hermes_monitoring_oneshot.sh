#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT_DIR/config/ai-tools.lock"
HERMES_HOME="$ROOT_DIR/artifacts/hermes_home"
HERMES_INSTALL_DIR="$ROOT_DIR/artifacts/hermes_agent"
HERMES_RUNNER="$HERMES_INSTALL_DIR/venv/bin/python"
prompt=''
output=''
image=''
reasoning='low'
while [[ $# -gt 0 ]]; do
  case "$1" in
    --prompt) prompt="$2"; shift 2 ;;
    --output) output="$2"; shift 2 ;;
    --image) image="$2"; shift 2 ;;
    --reasoning) reasoning="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done
if [[ ! -s "$prompt" || -z "$output" ]]; then
  echo 'Usage: hermes_monitoring_oneshot.sh --prompt FILE --output FILE [--image FILE] [--reasoning LEVEL]' >&2
  exit 2
fi
args=(chat --query-file "$prompt" --oneshot -Q --provider custom:a1-local
  --model "$AI_MODEL_FILE" --reasoning "$reasoning" --max-turns 1
  --run-budget 900 --ignore-rules --source tool)
if [[ -n "$image" ]]; then
  [[ -s "$image" ]] || { echo "Image is missing: $image" >&2; exit 2; }
  args+=(--image "$image")
fi

exec /usr/bin/env -i \
  HOME="$HOME" \
  USER="${USER:-}" \
  PATH="$PATH" \
  LANG="${LANG:-en_US.UTF-8}" \
  HERMES_HOME="$HERMES_HOME" \
  HERMES_VISION_MAX_CONCURRENCY=1 \
  NO_PROXY="127.0.0.1,localhost" \
  no_proxy="127.0.0.1,localhost" \
  "$HERMES_RUNNER" "$HERMES_INSTALL_DIR/hermes" "${args[@]}" >"$output"
