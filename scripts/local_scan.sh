#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [ -x .venv312/bin/python ]; then
  PYTHON=.venv312/bin/python
elif [ -x .venv/bin/python ]; then
  PYTHON=.venv/bin/python
else
  echo "Python environment not found. Run ./scripts/setup.sh first." >&2
  exit 1
fi

if command -v docker-compose >/dev/null; then
  docker-compose up -d db backup >/dev/null
else
  docker compose up -d db backup >/dev/null
fi
exec "$PYTHON" scripts/local_scan.py "$@"
