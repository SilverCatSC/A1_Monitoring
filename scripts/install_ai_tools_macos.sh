#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT_DIR/config/ai-tools.lock"
HERMES_HOME="$ROOT_DIR/artifacts/hermes_home"
HERMES_MAINTENANCE_HOME="$ROOT_DIR/artifacts/hermes_maintenance_home"
HERMES_OUROBOROS_LLM_HOME="$ROOT_DIR/artifacts/hermes_ouroboros_llm_home"
HERMES_INSTALL_DIR="$ROOT_DIR/artifacts/hermes_agent"
HERMES_RUNNER="$HERMES_INSTALL_DIR/venv/bin/python"
OUROBOROS_HOME="$ROOT_DIR/artifacts/ouroboros_home"

if [[ "$(uname -s)" != Darwin || "$(uname -m)" != arm64 ]]; then
  echo "The configured local model contour requires Apple Silicon macOS." >&2
  exit 1
fi
if ! command -v brew >/dev/null 2>&1; then
  echo "Run scripts/install_macos_prerequisites.sh first." >&2
  exit 1
fi

export HOMEBREW_NO_AUTO_UPDATE=1
for formula in uv llama.cpp hf; do
  if ! brew list --formula "$formula" >/dev/null 2>&1; then
    brew install "$formula"
  fi
done
if ! command -v tirith >/dev/null 2>&1; then
  brew install sheeki03/tap/tirith
fi

current_commit=''
if [[ -d "$HERMES_INSTALL_DIR/.git" ]]; then
  current_commit="$(git -C "$HERMES_INSTALL_DIR" rev-parse HEAD 2>/dev/null || true)"
fi
if [[ "$current_commit" != "$HERMES_COMMIT" || ! -x "$HERMES_RUNNER" ]]; then
  installer_file="$(mktemp)"
  trap 'rm -f "$installer_file"' EXIT
  curl -fsSL https://hermes-agent.nousresearch.com/install.sh -o "$installer_file"
  /bin/bash -n "$installer_file"
  /bin/bash "$installer_file" \
    --dir "$HERMES_INSTALL_DIR" \
    --hermes-home "$HERMES_HOME" \
    --commit "$HERMES_COMMIT" \
    --force-commit \
    --skip-setup \
    --skip-browser \
    --skip-computer-use \
    --no-skills
fi

mkdir -p \
  "$HERMES_HOME" \
  "$HERMES_MAINTENANCE_HOME" \
  "$HERMES_OUROBOROS_LLM_HOME" \
  "$OUROBOROS_HOME/.ouroboros"
chmod 700 "$HERMES_HOME"
chmod 700 "$HERMES_MAINTENANCE_HOME"
chmod 700 "$HERMES_OUROBOROS_LLM_HOME"
chmod 700 "$OUROBOROS_HOME" "$OUROBOROS_HOME/.ouroboros"
if [[ ! -s "$OUROBOROS_HOME/.local/share/tirith/tirith-threatdb.dat" ]]; then
  HOME="$OUROBOROS_HOME" tirith threat-db update
fi
install -m 600 "$ROOT_DIR/config/hermes-monitoring.yaml" "$HERMES_HOME/config.yaml"
install -m 600 "$ROOT_DIR/config/hermes-maintenance.yaml" "$HERMES_MAINTENANCE_HOME/config.yaml"
install -m 600 \
  "$ROOT_DIR/config/hermes-ouroboros-llm.yaml" \
  "$HERMES_OUROBOROS_LLM_HOME/config.yaml"
chmod 600 "$HERMES_HOME/config.yaml"
: > "$HERMES_HOME/.env"
chmod 600 "$HERMES_HOME/.env"
: > "$HERMES_MAINTENANCE_HOME/.env"
chmod 600 "$HERMES_MAINTENANCE_HOME/.env"
: > "$HERMES_OUROBOROS_LLM_HOME/.env"
chmod 600 "$HERMES_OUROBOROS_LLM_HOME/.env"

uv tool install --python 3.12 --force "ouroboros-ai[mcp]==${OUROBOROS_VERSION}"

escaped_root="${ROOT_DIR//\\/\\\\}"
escaped_root="${escaped_root//|/\\|}"
sed "s|__PROJECT_ROOT__|$escaped_root|g" \
  "$ROOT_DIR/config/ouroboros-local.yaml.template" \
  > "$OUROBOROS_HOME/.ouroboros/config.yaml"
chmod 600 "$OUROBOROS_HOME/.ouroboros/config.yaml"

export HERMES_HOME
export PATH="$HOME/.local/bin:$PATH"
export OUROBOROS_TELEMETRY=0
export DO_NOT_TRACK=1
"$HERMES_RUNNER" "$HERMES_INSTALL_DIR/hermes" --version
ouroboros --version
HOME="$OUROBOROS_HOME" ouroboros config validate
HOME="$OUROBOROS_HOME" ouroboros doctor install

echo "AI_TOOLS_INSTALLED"
echo "Hermes monitoring profile: $HERMES_HOME"
echo "Hermes maintenance profile: $HERMES_MAINTENANCE_HOME"
echo "Hermes Ouroboros LLM profile: $HERMES_OUROBOROS_LLM_HOME"
echo "Ouroboros project profile: $OUROBOROS_HOME/.ouroboros"
echo "Next: ./scripts/download_local_model_macos.sh"
echo "Ouroboros is separated from scans and uses an isolated maintenance profile."
