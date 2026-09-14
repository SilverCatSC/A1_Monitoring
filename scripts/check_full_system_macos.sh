#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT_DIR/config/ai-tools.lock"
HERMES_INSTALL_DIR="$ROOT_DIR/artifacts/hermes_agent"
OUROBOROS_HOME="$ROOT_DIR/artifacts/ouroboros_home"

"$ROOT_DIR/scripts/install_macos_prerequisites.sh" --check-only
"$ROOT_DIR/.venv312/bin/python" "$ROOT_DIR/scripts/doctor.py" --http
test -x "$HERMES_INSTALL_DIR/venv/bin/python"
command -v uv >/dev/null
command -v ouroboros >/dev/null
command -v llama-server >/dev/null
command -v tirith >/dev/null
test -x "$ROOT_DIR/scripts/hermes_maintenance_macos.sh"
test -s "$ROOT_DIR/artifacts/hermes_home/config.yaml"
test -s "$ROOT_DIR/artifacts/hermes_maintenance_home/config.yaml"
test -s "$ROOT_DIR/artifacts/hermes_ouroboros_llm_home/config.yaml"
test -s "$OUROBOROS_HOME/.ouroboros/config.yaml"
test -s "$OUROBOROS_HOME/.local/share/tirith/tirith-threatdb.dat"
HOME="$OUROBOROS_HOME" OUROBOROS_TELEMETRY=0 DO_NOT_TRACK=1 \
  ouroboros config validate >/dev/null
ouroboros_config="$(
  HOME="$OUROBOROS_HOME" OUROBOROS_TELEMETRY=0 DO_NOT_TRACK=1 \
    ouroboros config show
)"
[[ "$ouroboros_config" == *hermes* ]]
test -s "$ROOT_DIR/artifacts/models/$AI_MODEL_FILE"
test -s "$ROOT_DIR/artifacts/models/$AI_MODEL_FILE.sha256"
(cd "$ROOT_DIR/artifacts/models" && shasum -a 256 -c "$AI_MODEL_FILE.sha256")
test -s "$ROOT_DIR/artifacts/models/$AI_MODEL_MMPROJ_FILE"
test -s "$ROOT_DIR/artifacts/models/$AI_MODEL_MMPROJ_FILE.sha256"
(cd "$ROOT_DIR/artifacts/models" && shasum -a 256 -c "$AI_MODEL_MMPROJ_FILE.sha256")
curl -fsS --max-time 5 "http://127.0.0.1:$AI_MODEL_PORT/v1/models" >/dev/null
echo "FULL_MONITORING_SYSTEM_READY"
