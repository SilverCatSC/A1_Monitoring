#!/bin/bash
# One manual action; the protected host runner performs every live operation.
set -euo pipefail
cd "$(dirname "$0")"
export SELLER_PREFLIGHT_PAGES="${SELLER_PREFLIGHT_PAGES:-10}"
export CATALOGUE_LAZY_LOAD_MAX_BATCHES="${CATALOGUE_LAZY_LOAD_MAX_BATCHES:-10}"
export PYTHONPATH="$PWD/src:$PWD"
if [ -x .venv312/bin/python ]; then
  exec .venv312/bin/python scripts/recover_monitoring.py
else
  exec .venv/bin/python scripts/recover_monitoring.py
fi
