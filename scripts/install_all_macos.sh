#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SKIP_MODEL=false
if [[ "${1:-}" == '--skip-model' ]]; then
  SKIP_MODEL=true
elif [[ $# -gt 0 ]]; then
  echo "Usage: $0 [--skip-model]" >&2
  exit 64
fi

"$ROOT_DIR/scripts/install_macos_prerequisites.sh"
"$ROOT_DIR/scripts/setup.sh"
"$ROOT_DIR/scripts/install_ai_tools_macos.sh"
if ! $SKIP_MODEL; then
  "$ROOT_DIR/scripts/download_local_model_macos.sh"
  "$ROOT_DIR/scripts/start_local_ai.sh"
fi

echo "A1_MONITORING_SYSTEM_INSTALLED"
echo "Dashboard: http://127.0.0.1:18000/api/v1/dashboard"
if $SKIP_MODEL; then
  echo "AI tools are installed, but the model is not. Run scripts/download_local_model_macos.sh."
fi
