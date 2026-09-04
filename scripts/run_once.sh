#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

source .venv/bin/activate 2>/dev/null || true

if [ -n "${SOURCE_CSV_PATH:-}" ]; then
  python -m app.cli import-source --path "$SOURCE_CSV_PATH"
fi

python -m app.cli scan
echo "[run_once] scan cycle finished"

