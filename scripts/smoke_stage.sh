#!/usr/bin/env bash
# Read-only application checks. No source import, marketplace scan or database restore.
set -euo pipefail
cd "$(dirname "$0")/.."
.venv312/bin/python scripts/doctor.py --http
