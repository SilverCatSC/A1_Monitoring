#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT_DIR/config/ai-tools.lock"
MODEL_PATH="$ROOT_DIR/artifacts/models/$AI_MODEL_FILE"
MMPROJ_PATH="$ROOT_DIR/artifacts/models/$AI_MODEL_MMPROJ_FILE"
STATE_DIR="$ROOT_DIR/artifacts/ai_runtime"
PID_FILE="$STATE_DIR/llama-server.pid"
LOG_FILE="$STATE_DIR/llama-server.log"
BASE_URL="http://127.0.0.1:$AI_MODEL_PORT"

if curl -fsS --max-time 2 "$BASE_URL/v1/models" >/dev/null 2>&1; then
  echo "LOCAL_AI_REUSED $BASE_URL"
  exit 0
fi
if [[ ! -s "$MODEL_PATH" ]]; then
  echo "Local model is missing. Run scripts/download_local_model_macos.sh." >&2
  exit 1
fi
if [[ ! -s "$MMPROJ_PATH" ]]; then
  echo "Local vision projector is missing. Run scripts/download_local_model_macos.sh." >&2
  exit 1
fi
if ! command -v llama-server >/dev/null 2>&1; then
  echo "llama-server is missing. Run scripts/install_ai_tools_macos.sh." >&2
  exit 1
fi

mkdir -p "$STATE_DIR"
nohup nice -n 10 llama-server \
  -m "$MODEL_PATH" \
  --mmproj "$MMPROJ_PATH" \
  -ngl 99 \
  -c "$AI_MODEL_CONTEXT" \
  -np 1 \
  -t "$AI_MODEL_THREADS" \
  -tb "$AI_MODEL_BATCH_THREADS" \
  -b "$AI_MODEL_BATCH_SIZE" \
  -ub "$AI_MODEL_UBATCH_SIZE" \
  -n "$AI_MODEL_MAX_OUTPUT_TOKENS" \
  --threads-http 2 \
  --poll 0 \
  --poll-batch 0 \
  -fa on \
  --cache-type-k q4_0 \
  --cache-type-v q4_0 \
  --host 127.0.0.1 \
  --port "$AI_MODEL_PORT" \
  --cors-origins localhost \
  --no-cors-credentials \
  --no-webui \
  --reasoning auto \
  --reasoning-budget "$AI_MODEL_REASONING_BUDGET" \
  >"$LOG_FILE" 2>&1 &
server_pid=$!
printf '%s\n' "$server_pid" > "$PID_FILE"

for _attempt in $(seq 1 120); do
  if curl -fsS --max-time 2 "$BASE_URL/v1/models" >/dev/null 2>&1; then
    echo "LOCAL_AI_READY $BASE_URL pid=$server_pid"
    exit 0
  fi
  if ! kill -0 "$server_pid" 2>/dev/null; then
    echo "llama-server stopped during startup. See $LOG_FILE" >&2
    exit 1
  fi
  sleep 2
done

echo "llama-server did not become ready. See $LOG_FILE" >&2
exit 1
