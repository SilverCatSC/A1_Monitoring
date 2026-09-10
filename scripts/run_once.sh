#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [ -f .venv312/bin/activate ]; then
  source .venv312/bin/activate
elif [ -f .venv/bin/activate ]; then
  source .venv/bin/activate
fi

python -m app.cli run-cycle
echo "[run_once] import and scan cycle finished"
