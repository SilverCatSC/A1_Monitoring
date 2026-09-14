#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p artifacts
./scripts/start_local.sh
open http://127.0.0.1:18000/api/v1/dashboard
caffeinate -i ./scripts/run_full_monitoring_macos.sh
