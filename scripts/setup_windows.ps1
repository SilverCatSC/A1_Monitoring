[CmdletBinding()]
param(
    [switch]$SkipDocker,
    [switch]$SkipPythonInstall
)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $Root
. (Join-Path $PSScriptRoot 'windows_common.ps1')
$env:PYTHONUTF8 = '1'

function Require-Command([string]$Name, [string]$Hint) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name не найден. $Hint"
    }
}

Require-Command 'git' 'Установите Git for Windows и повторите команду.'
if (-not $SkipDocker) {
    Require-Command 'docker' 'Установите Docker Desktop и включите WSL2 backend.'
    Invoke-A1Native 'docker' @('compose', 'version')
}

$Python = Join-Path $Root '.venv312\Scripts\python.exe'
if (-not (Test-Path $Python)) {
    if ($SkipPythonInstall) {
        throw '.venv312 отсутствует, а -SkipPythonInstall указан.'
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        Invoke-A1Native 'py' @('-3.12', '-m', 'venv', (Join-Path $Root '.venv312'))
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        Invoke-A1Native 'python' @('-c', 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)')
        Invoke-A1Native 'python' @('-m', 'venv', (Join-Path $Root '.venv312'))
    } else {
        throw 'Нужен Python 3.12. Установите Python с опцией Add Python to PATH.'
    }
}

Invoke-A1Native $Python @('-c', 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)')
Invoke-A1Native $Python @('-m', 'pip', 'install', '-r', 'requirements.lock')
Invoke-A1Native $Python @('-m', 'pip', 'install', '--no-deps', '--no-build-isolation', '-e', '.')

if (-not (Test-Path '.env')) {
    Invoke-A1Native $Python @('scripts/prepare_local.py')
}
function Ensure-EnvLine([string]$Name, [string]$Value) {
    $envPath = Join-Path $Root '.env'
    $lines = @(Get-Content -LiteralPath $envPath -Encoding utf8 -ErrorAction Stop)
    if (-not ($lines -match "^$Name=")) {
        Add-Content -LiteralPath $envPath -Encoding utf8 -Value "$Name=$Value"
    }
}
Ensure-EnvLine 'APP_BIND_PORT' '18000'
Ensure-EnvLine 'DB_BIND_PORT' '15433'
if (-not (Test-Path 'artifacts\evidence')) {
    New-Item -ItemType Directory -Path 'artifacts\evidence' -Force | Out-Null
}

if (-not $SkipDocker) {
    Invoke-A1Native 'docker' @('compose', 'up', '-d', '--build', 'app', 'db', 'backup')
    Invoke-A1Native $Python @('scripts/doctor.py', '--http', '--wait')
} else {
    Invoke-A1Native $Python @('scripts/doctor.py')
}

Write-Host ''
if ($SkipDocker) {
    Write-Host 'WINDOWS_PYTHON_READY - application not started (-SkipDocker).'
    return
}
$BaseUrl = Get-A1BaseUrl $Root
Write-Host 'WINDOWS_SETUP_OK'
Write-Host "Dashboard: $BaseUrl/api/v1/dashboard"
Write-Host 'Следующий шаг: .\scripts\install_ai_tools_windows.ps1'
