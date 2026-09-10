[CmdletBinding()]
param(
    [ValidateSet('auto_ru,avito','auto_ru','avito')]
    [string]$Engines = 'auto_ru,avito',
    [ValidateRange(1,10)]
    [int]$Pages = 3,
    [ValidateSet('normal','cautious')]
    [string]$Pace = 'cautious',
    [switch]$Watch,
    [ValidateRange(1,525600)]
    [int]$IntervalMinutes = 360
)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $Root
. (Join-Path $PSScriptRoot 'windows_common.ps1')
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
$Python = Join-Path $Root '.venv312\Scripts\python.exe'
if (-not (Test-Path $Python)) { throw 'Окружение не найдено. Выполните .\scripts\setup_windows.ps1.' }

Invoke-A1Native 'docker' @('compose', 'up', '-d', 'db', 'backup')
$arguments = @('scripts/local_scan.py', '--engines', $Engines, '--pages', $Pages, '--pace', $Pace)
if ($Watch) { $arguments += @('--watch', '--interval-minutes', $IntervalMinutes) }
& $Python @arguments
$exitCode = $LASTEXITCODE
if ($exitCode -eq 2) {
    Write-Warning 'LOCAL_SCAN_PARTIAL: есть технические ошибки или ссылки требуют ручного подтверждения.'
}
exit $exitCode
