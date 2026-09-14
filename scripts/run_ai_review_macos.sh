#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"
if [[ $# -ne 2 || "$1" != '--cycle-id' || -z "$2" ]]; then
  echo 'Usage: run_ai_review_macos.sh --cycle-id <completed-cycle-id>' >&2
  exit 2
fi
CYCLE_ID="$2"
# shellcheck disable=SC1091
source "$ROOT_DIR/config/ai-tools.lock"
INPUT_FILE="$ROOT_DIR/artifacts/agent_reviews/live_packet_latest.json"

"$ROOT_DIR/scripts/sync_ai_profiles_macos.sh"
ai_was_running=false
if curl -fsS --max-time 2 "http://127.0.0.1:$AI_MODEL_PORT/v1/models" >/dev/null 2>&1; then
  ai_was_running=true
fi
"$ROOT_DIR/scripts/start_local_ai.sh"
cleanup_ai() {
  if ! $ai_was_running; then
    "$ROOT_DIR/scripts/stop_local_ai.sh" || true
  fi
}
trap cleanup_ai EXIT

# The packet builder verifies the exact cycle through scripts/local_api.py. It
# keeps a local Basic-auth header in memory when AUTH_ENABLED=true, rather than
# exposing a credential in curl arguments or shell output.
"$ROOT_DIR/.venv312/bin/python" "$ROOT_DIR/scripts/build_live_agent_packet.py" --cycle-id "$CYCLE_ID"
"$ROOT_DIR/.venv312/bin/python" "$ROOT_DIR/scripts/build_ai_work_units.py"

set +e
"$ROOT_DIR/.venv312/bin/python" "$ROOT_DIR/scripts/run_ai_work_units.py" \
  --cycle-id "$CYCLE_ID" \
  --cooldown-seconds "$AI_STAGE_COOLDOWN_SECONDS"
review_status=$?
set -e

if [[ ! -s "$ROOT_DIR/artifacts/agent_reviews/latest.json" ]]; then
  echo 'AI_STAGED_REVIEW_FAILED final review is missing' >&2
  exit 2
fi
"$ROOT_DIR/.venv312/bin/python" "$ROOT_DIR/scripts/validate_ai_review.py" \
  --normalize --allowed-vehicles-from "$INPUT_FILE" \
  "$ROOT_DIR/artifacts/agent_reviews/latest.json"
PYTHONPATH="$ROOT_DIR" "$ROOT_DIR/.venv312/bin/python" \
  "$ROOT_DIR/scripts/validate_staged_review.py" \
  --cycle-id "$CYCLE_ID" \
  "$ROOT_DIR/artifacts/agent_reviews/staged_review_latest.json"

if [[ $review_status -ne 0 ]]; then
  echo "AI_STAGED_REVIEW_PARTIAL status=$review_status" >&2
  exit 2
fi
echo "AI_REVIEW_READY $ROOT_DIR/artifacts/agent_reviews/latest.json"
