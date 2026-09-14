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
  checksum="$latest.sha256"
  if [ ! -f "$checksum" ]; then
    echo "Backup checksum is missing; create a fresh verified backup before restore-test." >&2
    exit 1
  fi
  (cd /backups && sha256sum -c "$(basename "$checksum")")
  pg_restore --list "$latest" >/dev/null
  cleanup() {
    dropdb --if-exists --force "$RESTORE_DB" >/dev/null 2>&1 || true
  }
  trap cleanup EXIT INT TERM
  createdb "$RESTORE_DB"
  pg_restore --exit-on-error --no-owner --no-privileges --dbname="$RESTORE_DB" "$latest"

  verified_tables=""
  for table in listings source_import_snapshots monitoring_cycles manager_feedback; do
    source_exists=$(psql --tuples-only --no-align --command="SELECT to_regclass('\''public.'\'' || '\''$table'\'') IS NOT NULL")
    if [ "$source_exists" != "t" ]; then
      printf "RESTORE_TABLE_SKIPPED table=%s source_missing=true\n" "$table"
      continue
    fi
    restored_exists=$(psql --dbname="$RESTORE_DB" --tuples-only --no-align --command="SELECT to_regclass('\''public.'\'' || '\''$table'\'') IS NOT NULL")
    if [ "$restored_exists" != "t" ]; then
      echo "Restore verification failed: table $table is absent from restored database." >&2
      exit 1
    fi
    source_count=$(psql --tuples-only --no-align --command="SELECT count(*) FROM $table")
    restored_count=$(psql --dbname="$RESTORE_DB" --tuples-only --no-align --command="SELECT count(*) FROM $table")
    if [ "$source_count" != "$restored_count" ]; then
      echo "Restore verification failed: row count differs for $table." >&2
      exit 1
    fi
    verified_tables="${verified_tables}${table}=${restored_count},"
  done
  source_revision=$(psql --tuples-only --no-align --command="SELECT version_num FROM alembic_version")
  restored_revision=$(psql --dbname="$RESTORE_DB" --tuples-only --no-align --command="SELECT version_num FROM alembic_version")
  if [ "$source_revision" != "$restored_revision" ]; then
    echo "Restore verification failed: Alembic revision differs." >&2
    exit 1
  fi
  printf "RESTORE_OK backup=%s revision=%s tables=%s\n" \
    "$latest" "$restored_revision" "${verified_tables%,}"
'
