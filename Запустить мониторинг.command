#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p artifacts

# Finder is deliberately a readiness-only entrypoint.  A double-click must
# never create marketplace observations, start AI work, or bypass the current
# M7/VPSUS admission gates.  The controlled browser cycle remains an explicit
# Terminal operation through run_monitoring_host_macos.sh after those gates.
./scripts/run_monitoring_host_macos.sh --preflight
open http://127.0.0.1:18000/api/v1/dashboard
printf '%s\n' 'FINDER_PREFLIGHT_OK cycle_not_started=true'
printf '%s\n' 'M7_CONTROLLED_CYCLE requires owner-approved VPSUS evidence; see docs/production/ACCEPTANCE_M7.md'
