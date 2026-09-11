[CmdletBinding()]
param(
    [ValidateSet('auto_ru,avito','auto_ru','avito')][string]$Engines = 'auto_ru,avito',
    [ValidateRange(1,10)][int]$Pages = 3,
    [ValidateSet('normal','cautious')][string]$Pace = 'normal',
    [ValidateSet('light','heavy')][string]$AiProfile = 'light',
    [ValidateRange(1024,65536)][int]$MinFreeMemoryMb = 7500,
    [ValidateRange(1,20)][int]$ReplacementChecks = 20,
    [switch]$AllowSwap,
    [switch]$RunHeavyReview
)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $Root
. (Join-Path $PSScriptRoot 'windows_common.ps1')
$python = Join-Path $Root '.venv312\Scripts\python.exe'
$powerShell = Get-A1PowerShellExecutable
$docker = Get-A1DockerExecutable
# Docker Compose 29 can fail with "failed to get console: The handle is invalid"
# when its interactive progress renderer runs inside Start-Transcript on
# Windows PowerShell 5.1. Plain progress is deterministic and transcript-safe.
$env:COMPOSE_PROGRESS = 'plain'
$env:BUILDKIT_PROGRESS = 'plain'
$env:CAPTCHA_WAIT_SECONDS = '0'
$env:CAPTCHA_RETRY_UNTIL_SUCCESS = 'true'
$env:CAPTCHA_RELOAD_SECONDS = '15'
$env:SELLER_DIRECT_CHECKS_LIMIT = [string]$ReplacementChecks
$cycleMutex = New-Object System.Threading.Mutex($false, 'Local\A1MonitoringFullCycle')
$cycleAcquired = $false
try {
    $cycleAcquired = $cycleMutex.WaitOne(0)
} catch [System.Threading.AbandonedMutexException] {
    $cycleAcquired = $true
}
if (-not $cycleAcquired) {
    $cycleMutex.Dispose()
    throw 'Another full monitoring cycle is already running. Wait for its PowerShell prompt to return.'
}
New-Item -ItemType Directory -Force artifacts | Out-Null
$log = Join-Path $Root ("artifacts\full_monitoring_{0}.log" -f (Get-Date -Format 'yyyyMMdd_HHmmss'))
$overall = 0
Start-Transcript -Path $log
try {
    Write-A1MemorySnapshot 'start' | Out-Null
    # Hermes participates in seller reconciliation before search, so the light
    # local model must already be available while the visible browser is open.
    $env:A1_REPLACEMENT_AI = '1'
    $env:A1_LOCAL_AI_URL = 'http://127.0.0.1:18080'
    & (Join-Path $PSScriptRoot 'start_local_ai_windows.ps1') -Profile $AiProfile -MinFreeMemoryMb $MinFreeMemoryMb -AllowSwap:$AllowSwap
    Invoke-A1Native $docker @('compose', 'up', '-d', 'app', 'db', 'backup')
    & $python scripts/local_scan.py --engines $Engines --pages $Pages --pace $Pace
    $scanStatus = $LASTEXITCODE
    if ($scanStatus -ne 0 -and $scanStatus -ne 2) { throw "Live scan failed (exit $scanStatus)." }
    if ($scanStatus -eq 2) { $overall = 2 }

    Invoke-A1Native $python @('scripts/audit_company_site.py')
    Invoke-A1Native $python @('scripts/audit_head_table.py')
    Invoke-A1Native $python @('scripts/build_live_agent_packet.py')
    Invoke-A1Native $python @('scripts/build_ai_work_units.py')

    & (Join-Path $PSScriptRoot 'stop_monitoring_chrome_windows.ps1')
    Invoke-A1Native $docker @('compose', 'stop', 'app', 'backup', 'db')
    Start-Sleep -Seconds 5
    Write-A1MemorySnapshot 'browser_and_docker_stopped' | Out-Null

    & $powerShell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'run_ai_review_windows.ps1') -PreparedPacket -Profile $AiProfile
    if ($LASTEXITCODE -ne 0) { $overall = 2 }
    if ($RunHeavyReview -and $AiProfile -ne 'heavy') {
        & (Join-Path $PSScriptRoot 'stop_local_ai_windows.ps1')
        & (Join-Path $PSScriptRoot 'start_local_ai_windows.ps1') -Profile heavy -MinFreeMemoryMb $MinFreeMemoryMb -AllowSwap:$AllowSwap
    }
    if ($RunHeavyReview) {
        $heavyOutputDir = Join-Path $Root 'artifacts\heavy_reviews'
        New-Item -ItemType Directory -Force $heavyOutputDir | Out-Null
        $heavyOutput = Join-Path $heavyOutputDir ("review_{0}.json" -f (Get-Date -Format 'yyyyMMdd_HHmmss'))
        & $python scripts/run_heavy_staged_audit.py artifacts\agent_reviews\staged_review_latest.json $heavyOutput
        if ($LASTEXITCODE -ne 0) { $overall = 2 }
        if (Test-Path $heavyOutput) {
            Copy-Item -Force $heavyOutput (Join-Path $heavyOutputDir 'latest.json')
        }
    }
} catch {
    Write-Warning $_
    $overall = 2
} finally {
    & (Join-Path $PSScriptRoot 'stop_local_ai_windows.ps1')
    & $docker compose up -d app db backup | Out-Host
    Write-A1MemorySnapshot 'finished' | Out-Null
    Stop-Transcript
    $cycleMutex.ReleaseMutex()
    $cycleMutex.Dispose()
}
if ($overall -ne 0) { Write-Warning "MONITORING_SYSTEM_PARTIAL_WINDOWS log=$log"; exit 2 }
Write-Host "MONITORING_SYSTEM_OK_WINDOWS log=$log"
