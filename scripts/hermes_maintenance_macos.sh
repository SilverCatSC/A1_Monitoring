#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
HERMES_HOME="$ROOT_DIR/artifacts/hermes_maintenance_home"
HERMES_INSTALL_DIR="$ROOT_DIR/artifacts/hermes_agent"
HERMES_RUNNER="$HERMES_INSTALL_DIR/venv/bin/python"

if [[ ! -x "$HERMES_RUNNER" ]]; then
  echo "Hermes is not installed. Run scripts/install_ai_tools_macos.sh." >&2
  exit 1
fi

# Ouroboros completion adapters include --max-turns and only need a text
# response. Keep those calls in a separate no-tools profile. Agent-runtime
# calls omit this option and receive the maintenance toolset in a worktree.
for argument in "$@"; do
  if [[ "$argument" == '--max-turns' ]]; then
    HERMES_HOME="$ROOT_DIR/artifacts/hermes_ouroboros_llm_home"
    break
  fi
done

exec /usr/bin/env -i \
  HOME="$HOME" \
  USER="${USER:-}" \
  LOGNAME="${LOGNAME:-${USER:-}}" \
  SHELL="${SHELL:-/bin/zsh}" \
  PATH="$PATH" \
  LANG="${LANG:-en_US.UTF-8}" \
  TERM="${TERM:-xterm-256color}" \
  TMPDIR="${TMPDIR:-/tmp}" \
  HERMES_HOME="$HERMES_HOME" \
  OUROBOROS_TELEMETRY=0 \
  DO_NOT_TRACK=1 \
  NO_PROXY="127.0.0.1,localhost" \
  no_proxy="127.0.0.1,localhost" \
  "$HERMES_RUNNER" "$HERMES_INSTALL_DIR/hermes" "$@"
