#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT_DIR/config/ai-tools.lock"
PID_FILE="$ROOT_DIR/artifacts/ai_runtime/llama-server.pid"
if [[ ! -f "$PID_FILE" ]]; then
  echo "LOCAL_AI_NOT_RUNNING"
  exit 0
fi
server_pid="$(tr -dc '0-9' < "$PID_FILE")"
if [[ -z "$server_pid" ]] || ! kill -0 "$server_pid" 2>/dev/null; then
  echo "LOCAL_AI_NOT_RUNNING"
  exit 0
fi
process_command="$(ps -p "$server_pid" -o command= 2>/dev/null || true)"
case "$process_command" in
  *llama-server*--port\ "$AI_MODEL_PORT"*) kill "$server_pid" ;;
  *) echo "PID $server_pid is not the A1 llama-server; refusing to stop it." >&2; exit 1 ;;
esac
echo "LOCAL_AI_STOPPED pid=$server_pid"
