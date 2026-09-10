#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"
# Optional override is intended for an isolated saved-artifact smoke test only.
PACKET="${OUROBOROS_PACKET:-$ROOT_DIR/artifacts/agent_reviews/staged_review_latest.json}"
OUTPUT_DIR="${OUROBOROS_OUTPUT_DIR:-$ROOT_DIR/artifacts/ouroboros_reviews}"
STAMP="$(date +%Y%m%d_%H%M%S)"
REPORT="$OUTPUT_DIR/review_$STAMP.txt"

if [[ ! -s "$PACKET" ]]; then
  echo 'Staged Hermes review is missing. Run scripts/run_ai_review_macos.sh first.' >&2
  exit 1
fi
mkdir -p "$OUTPUT_DIR"
PYTHONPATH="$ROOT_DIR" "$ROOT_DIR/.venv312/bin/python" \
  "$ROOT_DIR/scripts/validate_staged_review.py" "$PACKET"
"$ROOT_DIR/scripts/sync_ai_profiles_macos.sh"

ai_was_running=false
# shellcheck disable=SC1091
source "$ROOT_DIR/config/ai-tools.lock"
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

quality_bar='You are the independent local Ouroboros audit stage of A1 Monitoring. Review only the compact staged Hermes artifact. Do not repeat primary vehicle analysis. Audit coverage, schema, provenance, contradictions between data and vision findings, technical failures, and whether final Hermes issues are supported by unit_reports. PASS only if every recommendation ties to saved evidence and coverage. A vehicle can have no VIN, so use vehicle_key. Russian content is expected. Do not audit source control, runtime probes, UI, localization, or fields absent from this artifact. Never invent a fact, never treat partial or technical scan as absence, never access a browser, network, or other files, and never propose automatic data or code changes.'

set +e
"$ROOT_DIR/scripts/run_ouroboros_maintenance_macos.sh" qa "$PACKET" \
  --artifact-type api_response \
  --quality-bar "$quality_bar" \
  --pass-threshold 0.85 > "$REPORT" 2>&1
status=$?
set -e
if [[ ! -s "$REPORT" ]]; then
  echo "OUROBOROS_LIVE_AUDIT_FAILED status=$status file=$REPORT" >&2
  exit 2
fi

score="$(sed -nE 's/^Score: ([0-9]+\.[0-9]+) \/ 1\.00.*/\1/p' "$REPORT" | head -n 1)"
if [[ -n "$score" ]] && ! .venv312/bin/python - "$score" <<'PY'
import sys
raise SystemExit(0 if float(sys.argv[1]) >= 0.85 else 1)
PY
then
  echo "OUROBOROS_LIVE_AUDIT_BELOW_THRESHOLD score=$score threshold=0.85 file=$REPORT" >&2
  exit 2
fi
if [[ $status -ne 0 ]]; then
  echo "OUROBOROS_LIVE_AUDIT_FAILED status=$status file=$REPORT" >&2
  exit 2
fi

# Ouroboros produces prose. Deterministically reject a pass that ignores a
# failed or skipped vision work unit.
vision_gap="$(python3 - "$PACKET" <<'PY'
import json
import sys

packet = json.load(open(sys.argv[1], encoding='utf-8'))
coverage = packet.get('coverage', {})
print(max(0, int(coverage.get('vision_expected', 0)) - int(coverage.get('vision_completed', 0))))
PY
)"
if [[ "$vision_gap" -gt 0 ]] && grep -Eiq '\[PASS\]|Verdict: pass' "$REPORT"; then
  echo "OUROBOROS_LIVE_AUDIT_UNRELIABLE vision_gap=$vision_gap file=$REPORT" >&2
  exit 2
fi

cp "$REPORT" "$OUTPUT_DIR/latest.txt"
echo "OUROBOROS_LIVE_AUDIT_READY $REPORT"
