#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"
OUROBOROS_HOME="$ROOT_DIR/artifacts/ouroboros_home"

if [[ $# -eq 0 ]]; then
  echo "Usage: ./scripts/run_ouroboros_maintenance_macos.sh <ouroboros arguments>" >&2
  echo "Example: ./scripts/run_ouroboros_maintenance_macos.sh doctor install" >&2
  exit 2
fi
OUROBOROS_BIN="$(command -v ouroboros || true)"
if [[ -z "$OUROBOROS_BIN" ]]; then
  echo "Ouroboros is not installed. Run scripts/install_ai_tools_macos.sh." >&2
  exit 1
fi

export OUROBOROS_TELEMETRY=0
export DO_NOT_TRACK=1
export NO_PROXY="127.0.0.1,localhost"
export no_proxy="$NO_PROXY"

exec /usr/bin/env -i \
  HOME="$OUROBOROS_HOME" \
  USER="${USER:-}" \
  LOGNAME="${LOGNAME:-${USER:-}}" \
  SHELL="${SHELL:-/bin/zsh}" \
  PATH="$PATH" \
  LANG="${LANG:-en_US.UTF-8}" \
  TERM="${TERM:-xterm-256color}" \
  TMPDIR="${TMPDIR:-/tmp}" \
  OUROBOROS_TELEMETRY=0 \
  DO_NOT_TRACK=1 \
  NO_PROXY="$NO_PROXY" \
  no_proxy="$no_proxy" \
  "$OUROBOROS_BIN" "$@"
