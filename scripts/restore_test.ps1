[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $Root
. (Join-Path $PSScriptRoot 'windows_common.ps1')

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Docker Desktop is required for the PostgreSQL restore test.'
}

$RestoreDb = "a1_restore_verify_$((Get-Date).ToUniversalTime().ToString('yyyyMMddHHmmss'))"
if ($RestoreDb -notmatch '^a1_restore_verify_[0-9]{14}$') {
    throw 'Unsafe temporary restore database name.'
}

$restoreScript = @'
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
  source_exists=$(psql --tuples-only --no-align --command="SELECT to_regclass('public.' || '$table') IS NOT NULL")
  if [ "$source_exists" != "t" ]; then
    printf "RESTORE_TABLE_SKIPPED table=%s source_missing=true\n" "$table"
    continue
  fi
  restored_exists=$(psql --dbname="$RESTORE_DB" --tuples-only --no-align --command="SELECT to_regclass('public.' || '$table') IS NOT NULL")
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
printf "RESTORE_OK backup=%s revision=%s tables=%s\n" "$latest" "$restored_revision" "${verified_tables%,}"
'@

Invoke-A1Native 'docker' @(
    'compose', 'run', '--rm', '--no-deps', "--env=RESTORE_DB=$RestoreDb", 'backup',
    '/bin/sh', '-eu', '-c', $restoreScript
)
