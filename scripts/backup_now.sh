#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

if command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
elif docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
else
  echo "Compose command not found." >&2
  exit 1
fi

"${COMPOSE[@]}" run --rm --no-deps backup /bin/sh -eu -c '
  mkdir -p /backups
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  temporary="/backups/a1_search_monitor_${stamp}.dump.tmp"
  target="/backups/a1_search_monitor_${stamp}.dump"
  pg_dump --format=custom --file="$temporary"
  mv "$temporary" "$target"
  printf "%s\n" "$target"
'
