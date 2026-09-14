#!/usr/bin/env bash
# Deprecated acceptance entry point.
#
# The former implementation drove POST /api/v1/scan from the web container.
# That cannot prove or control the primary MacBook's visible host Chrome, its
# GUI/TCC state, or its VPSUS route. Keeping a callable legacy path would make
# it too easy to mistake a container request for M7 acceptance, so it is
# deliberately fail-closed before reading .env or contacting any local/external
# endpoint.

set -euo pipefail

cat >&2 <<'EOF'
LIVE_ACCEPTANCE_DEPRECATED reason=container_scan_not_valid_for_macos_primary_host
This legacy script does not read .env, call the local API, open Chrome, or start a marketplace scan.
For a no-marketplace MacBook readiness check, use:
  ./scripts/run_monitoring_host_macos.sh --preflight
For the only valid live acceptance sequence, follow:
  docs/production/ACCEPTANCE_M7.md
EOF
exit 2
