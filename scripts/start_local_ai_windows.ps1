[CmdletBinding()]
param([ValidateRange(1024,65536)][int]$MinFreeMemoryMb = 7500)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'windows_common.ps1')
$lock = Get-A1LockValues $Root
$baseUrl = "http://127.0.0.1:$($lock.AI_MODEL_PORT)"
try {
    Invoke-RestMethod -TimeoutSec 2 "$baseUrl/v1/models" | Out-Null
    Write-Host "LOCAL_AI_REUSED_WINDOWS $baseUrl"
    return
} catch {}

$freeMb = Write-A1MemorySnapshot 'before_ai'
if ($freeMb -lt $MinFreeMemoryMb) {
    throw "AI start blocked: free RAM ${freeMb} MB, required ${MinFreeMemoryMb} MB. Close applications; do not force swap-heavy execution."
}
$server = Join-Path $Root 'artifacts\llama_cpp_windows\llama-server.exe'
$model = Join-Path $Root "artifacts\models\$($lock.AI_MODEL_FILE)"
$mmproj = Join-Path $Root "artifacts\models\$($lock.AI_MODEL_MMPROJ_FILE)"
foreach ($path in @($server, $model, $mmproj)) { if (-not (Test-Path $path)) { throw "Missing AI component: $path" } }
$state = Join-Path $Root 'artifacts\ai_runtime'
New-Item -ItemType Directory -Force $state | Out-Null
$stdout = Join-Path $state 'llama-server-windows.log'
$stderr = Join-Path $state 'llama-server-windows-error.log'
$args = @(
    '-m', $model, '--mmproj', $mmproj, '-ngl', '0', '-c', $lock.AI_MODEL_CONTEXT,
    '-np', '1', '-t', $lock.AI_MODEL_THREADS, '-tb', $lock.AI_MODEL_BATCH_THREADS,
    '-b', $lock.AI_MODEL_BATCH_SIZE, '-ub', $lock.AI_MODEL_UBATCH_SIZE,
    '-n', $lock.AI_MODEL_MAX_OUTPUT_TOKENS, '--threads-http', '2', '--poll', '0',
    '--poll-batch', '0', '--cache-type-k', 'q4_0', '--cache-type-v', 'q4_0',
    '--host', '127.0.0.1', '--port', $lock.AI_MODEL_PORT, '--no-webui',
    '--reasoning', 'auto', '--reasoning-budget', $lock.AI_MODEL_REASONING_BUDGET
)
$process = Start-Process -FilePath $server -ArgumentList $args -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
$process.Id | Set-Content (Join-Path $state 'llama-server-windows.pid') -Encoding ascii
for ($attempt = 0; $attempt -lt 120; $attempt++) {
    Start-Sleep -Seconds 2
    if ($process.HasExited) { throw "llama-server stopped during startup. See $stderr" }
    try {
        Invoke-RestMethod -TimeoutSec 2 "$baseUrl/v1/models" | Out-Null
        Write-Host "LOCAL_AI_READY_WINDOWS $baseUrl pid=$($process.Id)"
        return
    } catch {}
}
throw "llama-server did not become ready. See $stderr"
