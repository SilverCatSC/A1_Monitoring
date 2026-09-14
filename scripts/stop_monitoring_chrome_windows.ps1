[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$profile = (Join-Path $Root 'artifacts\local_chrome_profile').ToLowerInvariant()
$matched = @(Get-CimInstance Win32_Process -Filter "Name = 'chrome.exe'" | Where-Object {
    $_.CommandLine -and $_.CommandLine.ToLowerInvariant().Contains('--remote-debugging-port=19222') -and
    $_.CommandLine.ToLowerInvariant().Contains($profile)
})
foreach ($process in $matched) { Stop-Process -Id $process.ProcessId -Force }
Write-Host "MONITORING_CHROME_STOPPED_WINDOWS processes=$($matched.Count)"
