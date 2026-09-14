#!/usr/bin/env bash
# Retired extended pipeline entry point.
#
# The historical script mixed a raw monitoring cycle with local AI stages and
# verbose logs. It did not carry the MacBook runner's lock, VPN-attestation or
# receipt contract through every stage. Production monitoring intentionally
# has one entry point: run_monitoring_host_macos.sh.

set -euo pipefail

echo 'MONITORING_SYSTEM_REFUSED reason=extended_pipeline_not_accepted' >&2
exit 64
