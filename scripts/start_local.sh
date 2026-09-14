#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if command -v docker-compose >/dev/null; then
  COMPOSE=(docker-compose)
else
  COMPOSE=(docker compose)
fi
"${COMPOSE[@]}" up -d --build app db backup
.venv312/bin/python scripts/doctor.py --http --wait
