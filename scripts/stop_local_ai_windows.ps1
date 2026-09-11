[CmdletBinding()]
param()

$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'windows_common.ps1')
$lock = Get-A1LockValues $Root
$pidFile = Join-Path $Root 'artifacts\ai_runtime\llama-server-windows.pid'
$managed = @(Get-A1LocalAiProcesses $Root $lock.AI_MODEL_PORT)
if (-not $managed.Count) {
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
    Write-Host 'LOCAL_AI_NOT_RUNNING_WINDOWS'
    return
}
foreach ($entry in $managed) {
    $serverPid = [int]$entry.ProcessId
    $process = Get-Process -Id $serverPid -ErrorAction SilentlyContinue
    if ($process) {
        Stop-Process -Id $serverPid
        $process.WaitForExit(15000) | Out-Null
    }
}
Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
Write-Host "LOCAL_AI_STOPPED_WINDOWS pids=$($managed.ProcessId -join ',')"
