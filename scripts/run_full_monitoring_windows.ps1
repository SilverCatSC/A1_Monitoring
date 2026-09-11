[CmdletBinding()]
param(
    [ValidateSet('auto_ru,avito','auto_ru','avito')][string]$Engines = 'auto_ru,avito',
    [ValidateRange(1,10)][int]$Pages = 3,
    [ValidateSet('normal','cautious')][string]$Pace = 'normal',
    [ValidateSet('light','heavy')][string]$AiProfile = 'light',
    [ValidateRange(1024,65536)][int]$MinFreeMemoryMb = 7500,
    [switch]$AllowSwap
)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $Root
. (Join-Path $PSScriptRoot 'windows_common.ps1')
$python = Join-Path $Root '.venv312\Scripts\python.exe'
$powerShell = Get-A1PowerShellExecutable
New-Item -ItemType Directory -Force artifacts | Out-Null
$log = Join-Path $Root ("artifacts\full_monitoring_{0}.log" -f (Get-Date -Format 'yyyyMMdd_HHmmss'))
$overall = 0
Start-Transcript -Path $log
try {
    Write-A1MemorySnapshot 'start' | Out-Null
    Invoke-A1Native 'docker' @('compose', 'up', '-d', 'app', 'db', 'backup')
    & $python scripts/local_scan.py --engines $Engines --pages $Pages --pace $Pace
    $scanStatus = $LASTEXITCODE
    if ($scanStatus -ne 0 -and $scanStatus -ne 2) { throw "Live scan failed (exit $scanStatus)." }
    if ($scanStatus -eq 2) { $overall = 2 }

    Invoke-A1Native $python @('scripts/audit_company_site.py')
    Invoke-A1Native $python @('scripts/audit_head_table.py')
    Invoke-A1Native $python @('scripts/build_live_agent_packet.py')
    Invoke-A1Native $python @('scripts/build_ai_work_units.py')

    & (Join-Path $PSScriptRoot 'stop_monitoring_chrome_windows.ps1')
    Invoke-A1Native 'docker' @('compose', 'stop', 'app', 'backup', 'db')
    Start-Sleep -Seconds 5
    Write-A1MemorySnapshot 'browser_and_docker_stopped' | Out-Null

    & (Join-Path $PSScriptRoot 'start_local_ai_windows.ps1') -Profile $AiProfile -MinFreeMemoryMb $MinFreeMemoryMb -AllowSwap:$AllowSwap
    & $powerShell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'run_ai_review_windows.ps1') -PreparedPacket -Profile $AiProfile
    if ($LASTEXITCODE -ne 0) { $overall = 2 }
    if ($AiProfile -ne 'heavy') {
        & (Join-Path $PSScriptRoot 'stop_local_ai_windows.ps1')
        & (Join-Path $PSScriptRoot 'start_local_ai_windows.ps1') -Profile heavy -MinFreeMemoryMb $MinFreeMemoryMb -AllowSwap:$AllowSwap
    }
    & $powerShell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'run_ouroboros_live_audit_windows.ps1')
    if ($LASTEXITCODE -ne 0) { $overall = 2 }
} catch {
    Write-Warning $_
    $overall = 2
} finally {
    & (Join-Path $PSScriptRoot 'stop_local_ai_windows.ps1')
    docker compose up -d app db backup | Out-Host
    Write-A1MemorySnapshot 'finished' | Out-Null
    Stop-Transcript
}
if ($overall -ne 0) { Write-Warning "MONITORING_SYSTEM_PARTIAL_WINDOWS log=$log"; exit 2 }
Write-Host "MONITORING_SYSTEM_OK_WINDOWS log=$log"
