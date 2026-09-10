#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -x .venv312/bin/python ]]; then
  if ! command -v python3.12 >/dev/null; then
    echo "Нужен Python 3.12. Установите его и повторите настройку." >&2
    exit 1
  fi
  python3.12 -m venv .venv312
fi
.venv312/bin/python -m pip install -r requirements.lock
.venv312/bin/python -m pip install --no-deps --no-build-isolation -e .
.venv312/bin/python scripts/prepare_local.py
./scripts/start_local.sh
.venv312/bin/python scripts/doctor.py
