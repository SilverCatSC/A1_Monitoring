[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $Root
$packet = Join-Path $Root 'artifacts\agent_reviews\staged_review_latest.json'
if (-not (Test-Path $packet)) { throw 'Staged Hermes review is missing.' }
$python = Join-Path $Root '.venv312\Scripts\python.exe'
$env:PYTHONPATH = $Root
& $python scripts/validate_staged_review.py $packet
if ($LASTEXITCODE -ne 0) { throw 'Staged review validation failed.' }
& (Join-Path $PSScriptRoot 'sync_ai_profiles_windows.ps1')
$ouroboros = Join-Path $Root 'artifacts\ai_tools\bin\ouroboros.exe'
if (-not (Test-Path $ouroboros)) { throw 'Ouroboros is not installed.' }
$env:HOME = Join-Path $Root 'artifacts\ouroboros_home'
$env:USERPROFILE = $env:HOME
$env:HERMES_HOME = Join-Path $Root 'artifacts\hermes_ouroboros_llm_home'
$env:PATH = (Join-Path $Root 'artifacts\hermes_agent\venv\Scripts') + [IO.Path]::PathSeparator + $env:PATH
$env:OUROBOROS_TELEMETRY = '0'
$env:DO_NOT_TRACK = '1'
$env:NO_PROXY = '127.0.0.1,localhost'
$outputDir = Join-Path $Root 'artifacts\ouroboros_reviews'
New-Item -ItemType Directory -Force $outputDir | Out-Null
$report = Join-Path $outputDir ("review_{0}.txt" -f (Get-Date -Format 'yyyyMMdd_HHmmss'))
$qualityBar = 'You are the independent local Ouroboros audit stage of A1 Monitoring. Review only the compact staged Hermes artifact. Audit coverage, schema, provenance, contradictions, technical failures, and support by saved evidence. A vehicle can have no VIN, so use vehicle_key. Never invent a fact, never treat partial or technical scan as absence, never access a browser, network, or other files, and never propose automatic data or code changes.'
& $ouroboros qa $packet --artifact-type api_response --quality-bar $qualityBar --pass-threshold 0.85 1> $report 2>&1
$status = $LASTEXITCODE
if (-not (Test-Path $report) -or (Get-Item $report).Length -eq 0) { throw 'Ouroboros report is missing.' }
Copy-Item -Force $report (Join-Path $outputDir 'latest.txt')
if ($status -ne 0) {
    Write-Warning "OUROBOROS_LIVE_AUDIT_FAILED status=$status file=$report"
    $fallback = Join-Path $outputDir ("heavy_fallback_{0}.json" -f (Get-Date -Format 'yyyyMMdd_HHmmss'))
    $env:A1_AI_PROFILE = 'heavy'
    & $python scripts/run_heavy_staged_audit.py $packet $fallback
    if ($LASTEXITCODE -ne 0) { exit 2 }
    Copy-Item -Force $fallback (Join-Path $outputDir 'latest.json')
    Write-Host "OUROBOROS_WINDOWS_FALLBACK_READY $fallback"
    exit 0
}
Write-Host "OUROBOROS_LIVE_AUDIT_READY_WINDOWS $report"
