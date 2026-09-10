[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)] [string]$ReportUrl,
    [string]$Message = '',
    [string]$WebhookUrl = '',
    [string]$DialogId = '',
    [switch]$Send
)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $Root
$Python = Join-Path $Root '.venv312\Scripts\python.exe'
if (-not (Test-Path $Python)) { throw 'Окружение не найдено. Выполните .\scripts\setup_windows.ps1.' }

$invokeArgs = @('scripts/bitrix_publish.py', '--report-url', $ReportUrl)
if ($Message) { $invokeArgs += @('--message', $Message) }
if ($WebhookUrl) { $invokeArgs += @('--webhook-url', $WebhookUrl) }
if ($DialogId) { $invokeArgs += @('--dialog-id', $DialogId) }
if ($Send) { $invokeArgs += '--send' }
& $Python @invokeArgs
exit $LASTEXITCODE
