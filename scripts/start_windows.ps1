[CmdletBinding()]
param([switch]$OpenDashboard)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $Root
. (Join-Path $PSScriptRoot 'windows_common.ps1')
$env:PYTHONUTF8 = '1'
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Docker Desktop не найден.' }

$Python = Join-Path $Root '.venv312\Scripts\python.exe'
if (-not (Test-Path $Python)) { throw 'Run scripts/setup_windows.ps1 first.' }
$BaseUrl = Get-A1BaseUrl $Root
Invoke-A1Native 'docker' @('compose', 'up', '-d', '--build', 'app', 'db', 'backup')
Invoke-A1Native $Python @('scripts/doctor.py', '--http', '--wait')
if ($OpenDashboard) { Start-Process "$BaseUrl/api/v1/dashboard" }
Write-Host "WINDOWS_APP_READY $BaseUrl/api/v1/dashboard"
