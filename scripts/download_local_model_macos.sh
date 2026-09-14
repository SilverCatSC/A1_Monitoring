#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT_DIR/config/ai-tools.lock"
MODEL_DIR="$ROOT_DIR/artifacts/models"
MODEL_PATH="$MODEL_DIR/$AI_MODEL_FILE"
MMPROJ_PATH="$MODEL_DIR/$AI_MODEL_MMPROJ_FILE"

if [[ -s "$MODEL_PATH" && -s "$MMPROJ_PATH" ]]; then
  echo "LOCAL_MULTIMODAL_MODEL_EXISTS model=$MODEL_PATH mmproj=$MMPROJ_PATH"
  exit 0
fi
if ! command -v hf >/dev/null 2>&1; then
  echo "The hf command is missing. Run scripts/install_ai_tools_macos.sh." >&2
  exit 1
fi

available_kb="$(df -Pk "$ROOT_DIR" | awk 'NR==2 {print $4}')"
required_kb=$((3 * 1024 * 1024))
if [[ -z "$available_kb" || "$available_kb" -lt "$required_kb" ]]; then
  echo "At least 3 GB of free disk space is required for missing model components." >&2
  exit 1
fi

mkdir -p "$MODEL_DIR"
if [[ ! -s "$MODEL_PATH" ]]; then
  echo "Downloading $AI_MODEL_FILE (about 5.3 GB) from $AI_MODEL_REPO..."
  hf download "$AI_MODEL_REPO" "$AI_MODEL_FILE" --local-dir "$MODEL_DIR"
  test -s "$MODEL_PATH"
  shasum -a 256 "$MODEL_PATH" > "$MODEL_PATH.sha256"
fi
if [[ ! -s "$MMPROJ_PATH" ]]; then
  echo "Downloading $AI_MODEL_MMPROJ_FILE (about 0.9 GB) for local image analysis..."
  hf download "$AI_MODEL_REPO" "$AI_MODEL_MMPROJ_FILE" --local-dir "$MODEL_DIR"
  test -s "$MMPROJ_PATH"
  shasum -a 256 "$MMPROJ_PATH" > "$MMPROJ_PATH.sha256"
fi
echo "LOCAL_MULTIMODAL_MODEL_READY model=$MODEL_PATH mmproj=$MMPROJ_PATH"
