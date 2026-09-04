#!/usr/bin/env bash
set -euo pipefail

echo "[setup] creating python virtual env if needed"
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
pip install -U pip
pip install -e .
python -m playwright install chromium
python -m app.cli init

echo "[setup] done"

