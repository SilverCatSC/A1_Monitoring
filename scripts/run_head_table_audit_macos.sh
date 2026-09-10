#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -x .venv312/bin/python ]]; then
  echo 'Python environment is missing. Run ./scripts/setup.sh first.' >&2
  exit 1
fi

# The audit is read-only but needs the same local database connection as the
# visible-Chrome worker.  Reuse the worker's environment preparation.
exec .venv312/bin/python scripts/audit_head_table.py
