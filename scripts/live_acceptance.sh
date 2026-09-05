#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

if [[ ! -f .env ]]; then
  echo "LIVE_ACCEPTANCE_BLOCKED reason=missing_.env" >&2
  exit 2
fi

# .env is intentionally parsed by Bash because the deployment scripts do the same.
# URLs containing '&' must therefore be quoted.
set -a
source .env
set +a

case "${NETWORK_PROFILE:-unknown}" in
  cloud_no_vpn)
    ;;
  local_no_vpn)
    if [[ "${ALLOW_LOCAL_NO_VPN:-false}" != "true" ]]; then
      echo "LIVE_ACCEPTANCE_BLOCKED reason=local_profile_requires_ALLOW_LOCAL_NO_VPN=true" >&2
      exit 2
    fi
    ;;
  *)
    echo "LIVE_ACCEPTANCE_BLOCKED reason=network_profile_${NETWORK_PROFILE:-unknown}" >&2
    exit 2
    ;;
esac

BASE_URL="${LIVE_ACCEPTANCE_BASE_URL:-http://127.0.0.1:${APP_BIND_PORT:-8000}/api/v1}"
CURL_AUTH=()
if [[ "${AUTH_ENABLED:-false}" == "true" ]]; then
  if [[ -z "${ADMIN_USERNAME:-}" || -z "${ADMIN_PASSWORD:-}" ]]; then
    echo "LIVE_ACCEPTANCE_BLOCKED reason=missing_basic_auth_credentials" >&2
    exit 2
  fi
  CURL_AUTH=(--user "${ADMIN_USERNAME}:${ADMIN_PASSWORD}")
fi

curl_json() {
  curl --fail --silent --show-error --max-time "${LIVE_HTTP_TIMEOUT_SECONDS:-1800}" \
    "${CURL_AUTH[@]}" "$@"
}

READY_JSON="$(curl_json "$BASE_URL/ready")"
FILTERS_JSON="$(curl_json "$BASE_URL/filters")"
CATALOG_JSON="$(curl_json "$BASE_URL/filters/catalog/status")"
BEFORE_JSON="$(curl_json "$BASE_URL/status/scans/latest")"

READY_JSON="$READY_JSON" FILTERS_JSON="$FILTERS_JSON" CATALOG_JSON="$CATALOG_JSON" \
SCAN_ENGINES="${SCAN_ENABLED_ENGINES:-auto_ru,avito}" python3 - <<'PY'
import json
import os

ready = json.loads(os.environ['READY_JSON'])
filters = json.loads(os.environ['FILTERS_JSON'])['filters']
catalog = json.loads(os.environ['CATALOG_JSON'])
engines = {item.strip() for item in os.environ['SCAN_ENGINES'].split(',') if item.strip()}
active = [item for item in filters if item['active'] and item['source'] in engines]

errors = []
if ready.get('status') != 'ready':
    errors.append('application_not_ready')
for source in sorted(engines):
    source_filters = [item for item in active if item['source'] == source]
    if not source_filters:
        errors.append(f'no_active_filters:{source}')
    if any(item['expectations'] <= 0 for item in source_filters):
        errors.append(f'filter_without_expectations:{source}')
selected_gaps = [item for item in catalog['unmatched'] if item['source'] in engines]
selected_ambiguous = [item for item in catalog['ambiguous'] if item['source'] in engines]
if selected_gaps:
    errors.append(f'unmatched_catalog_assignments:{len(selected_gaps)}')
if selected_ambiguous:
    errors.append(f'ambiguous_catalog_assignments:{len(selected_ambiguous)}')
if errors:
    raise SystemExit('LIVE_ACCEPTANCE_BLOCKED reason=' + ','.join(errors))
print(f'LIVE_PREFLIGHT_OK engines={len(engines)} filters={len(active)}')
PY

STARTED_EPOCH="$(date +%s)"
SCAN_JSON="$(curl_json --request POST "$BASE_URL/scan")"
AFTER_JSON="$(curl_json "$BASE_URL/status/scans/latest")"
FINISHED_EPOCH="$(date +%s)"
ELAPSED_SECONDS="$((FINISHED_EPOCH - STARTED_EPOCH))"

SCAN_JSON="$SCAN_JSON" BEFORE_JSON="$BEFORE_JSON" AFTER_JSON="$AFTER_JSON" \
FILTERS_JSON="$FILTERS_JSON" SCAN_ENGINES="${SCAN_ENABLED_ENGINES:-auto_ru,avito}" \
EXPECTED_NETWORK_PROFILE="$NETWORK_PROFILE" ELAPSED_SECONDS="$ELAPSED_SECONDS" python3 - <<'PY'
import json
import os

scan = json.loads(os.environ['SCAN_JSON'])
before = json.loads(os.environ['BEFORE_JSON'])
after = json.loads(os.environ['AFTER_JSON'])
filters = json.loads(os.environ['FILTERS_JSON'])['filters']
engines = {item.strip() for item in os.environ['SCAN_ENGINES'].split(',') if item.strip()}
network_profile = os.environ['EXPECTED_NETWORK_PROFILE']
active = [item for item in filters if item['active'] and item['source'] in engines]
before_ids = {item.get('source'): item.get('id') for item in before['runs']}
latest = {item.get('source'): item for item in after['runs']}
errors = []

if scan.get('status') != 'ok':
    errors.append('scan_endpoint_not_ok')
summary = scan.get('summary') or {}
if summary.get('filters_scanned') != len(active):
    errors.append(f'filters_scanned:{summary.get("filters_scanned")}!={len(active)}')
if summary.get('runs') != len(engines):
    errors.append(f'runs:{summary.get("runs")}!={len(engines)}')
if summary.get('technical_errors') != 0:
    errors.append(f'technical_errors:{summary.get("technical_errors")}')

for source in sorted(engines):
    run = latest.get(source)
    if not run:
        errors.append(f'missing_latest_run:{source}')
        continue
    if run.get('id') == before_ids.get(source):
        errors.append(f'run_not_advanced:{source}')
    if run.get('status') != 'success':
        errors.append(f'run_status:{source}:{run.get("status")}')
    if run.get('network_profile') != network_profile:
        errors.append(f'network_profile:{source}:{run.get("network_profile")}')
    if run.get('filters_total', 0) <= 0 or run.get('filters_ok') != run.get('filters_total'):
        errors.append(
            f'filter_completion:{source}:{run.get("filters_ok")}/{run.get("filters_total")}'
        )
    if run.get('technical_errors') != 0:
        errors.append(f'run_technical_errors:{source}:{run.get("technical_errors")}')
    if run.get('observations', 0) <= 0:
        errors.append(f'no_observations:{source}')
    if run.get('evidence_files', 0) <= 0:
        errors.append(f'no_evidence:{source}')
    if run.get('state_counts', {}).get('technical_error', 0) != 0:
        errors.append(f'technical_observations:{source}')

if errors:
    raise SystemExit('LIVE_ACCEPTANCE_FAILED reason=' + ','.join(errors))
print(
    'LIVE_ACCEPTANCE_OK '
    f'engines={len(engines)} filters={len(active)} '
    f'found={summary.get("found", 0)} '
    f'missed_uncertain={summary.get("missed_uncertain", 0)} '
    f'missed_confirmed={summary.get("missed_confirmed", 0)} '
    f'elapsed_seconds={os.environ["ELAPSED_SECONDS"]}'
)
PY

echo "REPORT_URL=${BASE_URL%/api/v1}/api/v1/dashboard"
