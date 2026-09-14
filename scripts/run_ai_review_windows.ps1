[CmdletBinding()]
param([switch]$PreparedPacket)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $Root
. (Join-Path $PSScriptRoot 'windows_common.ps1')
$lock = Get-A1LockValues $Root
$python = Join-Path $Root '.venv312\Scripts\python.exe'
& (Join-Path $PSScriptRoot 'sync_ai_profiles_windows.ps1')
if (-not $PreparedPacket) {
    Invoke-A1Native $python @('scripts/build_live_agent_packet.py')
    Invoke-A1Native $python @('scripts/build_ai_work_units.py')
}
& $python scripts/run_ai_work_units.py --helper scripts/hermes_monitoring_oneshot_windows.cmd --cooldown-seconds $lock.AI_STAGE_COOLDOWN_SECONDS
$status = $LASTEXITCODE
if (-not (Test-Path 'artifacts\agent_reviews\latest.json')) { throw 'Final Hermes review is missing.' }
& $python scripts/validate_ai_review.py --normalize --allowed-vehicles-from artifacts/agent_reviews/live_packet_latest.json artifacts/agent_reviews/latest.json
if ($LASTEXITCODE -ne 0) { throw 'Hermes review validation failed.' }
$env:PYTHONPATH = $Root
& $python scripts/validate_staged_review.py artifacts/agent_reviews/staged_review_latest.json
if ($LASTEXITCODE -ne 0) { throw 'Staged Hermes review validation failed.' }
if ($status -ne 0) { Write-Warning "AI_STAGED_REVIEW_PARTIAL status=$status"; exit 2 }
Write-Host "AI_REVIEW_READY_WINDOWS $Root\artifacts\agent_reviews\latest.json"
