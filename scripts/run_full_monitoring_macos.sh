#!/usr/bin/env bash
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"
mkdir -p artifacts
log_file="artifacts/manual_scan_$(date +%Y%m%d_%H%M%S).log"

set +e
./scripts/local_scan.sh --engines auto_ru,avito --pages 3 --pace cautious \
  2>&1 | tee "$log_file"
scan_status=${PIPESTATUS[0]}
set -e

if [[ $scan_status -ne 0 && $scan_status -ne 2 ]]; then
  echo "MONITORING_SYSTEM_FAILED scan_status=$scan_status log=$log_file" >&2
  exit "$scan_status"
fi

company_site_status=0
./scripts/run_company_site_audit_macos.sh 2>&1 | tee -a "$log_file" || company_site_status=$?

head_table_status=0
./scripts/run_head_table_audit_macos.sh 2>&1 | tee -a "$log_file" || head_table_status=$?

# Keep one bounded local-model process for both sequential agent stages. This
# avoids loading the 6.2 GB model twice while preserving one-request concurrency.
# shellcheck disable=SC1091
source "$ROOT_DIR/config/ai-tools.lock"
ai_was_running=false
if curl -fsS --max-time 2 "http://127.0.0.1:$AI_MODEL_PORT/v1/models" >/dev/null 2>&1; then
  ai_was_running=true
fi
ai_start_status=0
./scripts/start_local_ai.sh 2>&1 | tee -a "$log_file" || ai_start_status=$?

ai_status=0
ouroboros_status=0
if [[ $ai_start_status -eq 0 ]]; then
  ./scripts/run_ai_review_macos.sh 2>&1 | tee -a "$log_file" || ai_status=$?
  ./scripts/run_ouroboros_live_audit_macos.sh 2>&1 | tee -a "$log_file" || ouroboros_status=$?
else
  ai_status=$ai_start_status
  ouroboros_status=$ai_start_status
fi
if ! $ai_was_running; then
  ./scripts/stop_local_ai.sh 2>&1 | tee -a "$log_file" || true
fi

if [[ $scan_status -eq 2 || $company_site_status -ne 0 || $head_table_status -ne 0 || $ai_status -ne 0 || $ouroboros_status -ne 0 ]]; then
  echo "MONITORING_SYSTEM_PARTIAL scan_status=$scan_status company_site_status=$company_site_status head_table_status=$head_table_status ai_status=$ai_status ouroboros_status=$ouroboros_status log=$log_file"
  exit 2
fi

echo "MONITORING_SYSTEM_OK log=$log_file"
