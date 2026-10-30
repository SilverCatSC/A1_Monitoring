#!/bin/bash
# Manual, visible Mac run. No schedule, automatic retry, or VPN changes.
set -uo pipefail
cd "$(dirname "$0")" || exit 1
if [ "$#" -eq 1 ] && [ "$1" = '--preflight' ]; then
  exec ./scripts/run_monitoring_host_macos.sh --preflight
fi
export SELLER_PREFLIGHT_PAGES="${SELLER_PREFLIGHT_PAGES:-10}"
export CATALOGUE_LAZY_LOAD_MAX_BATCHES="${CATALOGUE_LAZY_LOAD_MAX_BATCHES:-10}"
./scripts/run_monitoring_host_macos.sh \
  --diagnostic-routing --engines auto_ru,avito --pages 5 \
  --captcha-wait-seconds -1 --pace normal "$@"
result=$?
if [ "$result" -eq 2 ]; then
  echo 'Monitoring finished with incomplete checks. Open the report; unverified is not inactive.'
elif [ "$result" -ne 0 ]; then
  echo 'Monitoring could not finish. The HOST_RUNNER_RECORD directory contains the failure details.'
fi
exit "$result"
