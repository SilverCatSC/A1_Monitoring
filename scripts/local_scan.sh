#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [ -x .venv312/bin/python ]; then
  PYTHON=.venv312/bin/python
elif [ -x .venv/bin/python ]; then
  PYTHON=.venv/bin/python
else
  echo "Python environment not found. Run ./scripts/setup.sh first." >&2
  exit 1
fi

probe_only=0
for argument in "$@"; do
  case "$argument" in
    --probe-url|--probe-url=*)
      probe_only=1
      break
      ;;
  esac
done

if [ "$(uname -s)" = 'Darwin' ] && [ "$probe_only" -eq 0 ]; then
  if [ "${A1_MONITORING_HOST_RUNNER_CONTEXT:-}" != '1' ] \
    || [ "${A1_MONITORING_HOST_LOCK_HELD:-}" != '1' ] \
    || ! [[ "${A1_MONITORING_HOST_LOCK_FD:-}" =~ ^[0-9]+$ ]]; then
    echo "LOCAL_SCAN_REFUSED reason=verified_interactive_host_runner_required" >&2
    exit 64
  fi
  if ! "$PYTHON" "$ROOT_DIR/scripts/with_monitoring_host_lock_macos.py" \
    --lock-path "$ROOT_DIR/artifacts/.monitoring_host_runner_macos.lock" \
    --verify-inherited-fd "$A1_MONITORING_HOST_LOCK_FD" >/dev/null 2>&1; then
    echo "LOCAL_SCAN_REFUSED reason=verified_host_lock_required" >&2
    exit 64
  fi
fi

# Prepare only the owner-private directory, never an approval record. This is
# done before service work so an interactive cycle has a dedicated private
# location for the owner's short-lived admission record.
if [ "$probe_only" -eq 0 ] && ! "$PYTHON" -m app.service.vpn_admission --prepare-directory \
  "$ROOT_DIR/artifacts/vpn_admission"; then
  echo "LOCAL_SCAN_REFUSED reason=vpn_admission_directory" >&2
  exit 1
fi

if command -v docker-compose >/dev/null; then
  docker-compose up -d db backup >/dev/null
else
  docker compose up -d db backup >/dev/null
fi
exec "$PYTHON" scripts/local_scan.py "$@"
