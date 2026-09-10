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

RESTORE_DB="a1_restore_verify_$(date -u +%Y%m%d%H%M%S)"
if [[ ! "$RESTORE_DB" =~ ^a1_restore_verify_[0-9]{14}$ ]]; then
  echo "Unsafe restore database name: $RESTORE_DB" >&2
  exit 1
fi

"${COMPOSE[@]}" run --rm --no-deps -e RESTORE_DB="$RESTORE_DB" backup /bin/sh -eu -c '
  latest=$(find /backups -maxdepth 1 -type f -name "a1_search_monitor_*.dump" | sort | tail -n 1)
  if [ -z "$latest" ]; then
    echo "No database backup found." >&2
    exit 1
  fi
  cleanup() {
    dropdb --if-exists --force "$RESTORE_DB" >/dev/null 2>&1 || true
  }
  trap cleanup EXIT INT TERM
  createdb "$RESTORE_DB"
  pg_restore --exit-on-error --no-owner --no-privileges --dbname="$RESTORE_DB" "$latest"

  source_listings=$(psql --tuples-only --no-align --command="SELECT count(*) FROM listings")
  restored_listings=$(psql --dbname="$RESTORE_DB" --tuples-only --no-align --command="SELECT count(*) FROM listings")
  source_snapshots=$(psql --tuples-only --no-align --command="SELECT count(*) FROM source_import_snapshots")
  restored_snapshots=$(psql --dbname="$RESTORE_DB" --tuples-only --no-align --command="SELECT count(*) FROM source_import_snapshots")
  if [ "$source_listings" != "$restored_listings" ] || [ "$source_snapshots" != "$restored_snapshots" ]; then
    echo "Restore verification failed: row counts differ." >&2
    exit 1
  fi
  printf "RESTORE_OK backup=%s listings=%s snapshots=%s\n" "$latest" "$restored_listings" "$restored_snapshots"
'
