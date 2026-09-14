[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $Root
. (Join-Path $PSScriptRoot 'windows_common.ps1')

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Docker Desktop is required for the PostgreSQL backup.'
}

$backupScript = @'
mkdir -p /backups
stamp=$(date -u +%Y%m%dT%H%M%SZ)
temporary="/backups/a1_search_monitor_${stamp}.dump.tmp"
target="/backups/a1_search_monitor_${stamp}.dump"
pg_dump --format=custom --file="$temporary"
mv "$temporary" "$target"
sha256sum "$target" > "$target.sha256.tmp"
mv "$target.sha256.tmp" "$target.sha256"
printf "BACKUP_OK backup=%s checksum=%s\n" "$target" "$(cut -d " " -f 1 "$target.sha256")"
'@

Invoke-A1Native 'docker' @('compose', 'run', '--rm', '--no-deps', 'backup', '/bin/sh', '-eu', '-c', $backupScript)
