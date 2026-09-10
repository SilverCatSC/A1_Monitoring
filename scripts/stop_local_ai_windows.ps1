[CmdletBinding()]
param()

$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pidFile = Join-Path $Root 'artifacts\ai_runtime\llama-server-windows.pid'
if (-not (Test-Path $pidFile)) { Write-Host 'LOCAL_AI_NOT_RUNNING_WINDOWS'; return }
$serverPid = [int](Get-Content $pidFile -Raw)
$process = Get-Process -Id $serverPid -ErrorAction SilentlyContinue
if ($process) { Stop-Process -Id $serverPid; $process.WaitForExit(15000) | Out-Null }
Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
Write-Host "LOCAL_AI_STOPPED_WINDOWS pid=$serverPid"
