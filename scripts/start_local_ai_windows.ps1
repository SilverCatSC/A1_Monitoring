[CmdletBinding()]
param(
    [ValidateSet('light','heavy')][string]$Profile = 'light',
    [ValidateRange(1024,65536)][int]$MinFreeMemoryMb = 7500,
    [switch]$AllowSwap
)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'windows_common.ps1')
$lock = Get-A1LockValues $Root
$baseUrl = "http://127.0.0.1:$($lock.AI_MODEL_PORT)"
$modelFile = $lock.AI_MODEL_FILE
$mmprojFile = $lock.AI_MODEL_MMPROJ_FILE
$context = $lock.AI_MODEL_CONTEXT
$maxOutputTokens = $lock.AI_MODEL_MAX_OUTPUT_TOKENS
$reasoningBudget = $lock.AI_MODEL_REASONING_BUDGET
$reasoningFormat = 'off'
if ($Profile -eq 'heavy') {
    $modelFile = $lock.AI_HEAVY_MODEL_FILE
    $mmprojFile = $lock.AI_HEAVY_MODEL_MMPROJ_FILE
    $context = $lock.AI_HEAVY_MODEL_CONTEXT
    $maxOutputTokens = $lock.AI_HEAVY_MODEL_MAX_OUTPUT_TOKENS
    $reasoningBudget = $lock.AI_HEAVY_MODEL_REASONING_BUDGET
    $reasoningFormat = 'auto'
}
$running = $null
try {
    $running = Invoke-RestMethod -TimeoutSec 2 "$baseUrl/v1/models"
} catch {}
if ($running) {
    $runningJson = $running | ConvertTo-Json -Depth 8
    if ($runningJson -notlike "*$modelFile*") {
        Write-Host "LOCAL_AI_DIFFERENT_MODEL_WINDOWS expected=$modelFile url=$baseUrl"
        Write-Host "Stop it first with .\scripts\stop_local_ai_windows.ps1."
        exit 1
    }
    Write-Host "LOCAL_AI_REUSED_WINDOWS profile=$Profile $baseUrl"
    return
}

$freeMb = Write-A1MemorySnapshot 'before_ai'
if ($freeMb -lt $MinFreeMemoryMb -and -not $AllowSwap) {
    throw "AI start blocked: free RAM ${freeMb} MB, required ${MinFreeMemoryMb} MB. Close applications; do not force swap-heavy execution."
}
if ($freeMb -lt $MinFreeMemoryMb -and $AllowSwap) {
    Write-Warning "AI start continuing with swap: free RAM ${freeMb} MB, recommended ${MinFreeMemoryMb} MB."
}
$server = Join-Path $Root 'artifacts\llama_cpp_windows\llama-server.exe'
$model = Join-Path $Root "artifacts\models\$modelFile"
$mmproj = Join-Path $Root "artifacts\models\$mmprojFile"
foreach ($path in @($server, $model, $mmproj)) { if (-not (Test-Path $path)) { throw "Missing AI component: $path" } }
$state = Join-Path $Root 'artifacts\ai_runtime'
New-Item -ItemType Directory -Force $state | Out-Null
$stdout = Join-Path $state 'llama-server-windows.log'
$stderr = Join-Path $state 'llama-server-windows-error.log'
$args = @(
    '-m', $model, '--mmproj', $mmproj, '-ngl', '0', '-c', $context,
    '-np', '1', '-t', $lock.AI_MODEL_THREADS, '-tb', $lock.AI_MODEL_BATCH_THREADS,
    '-b', $lock.AI_MODEL_BATCH_SIZE, '-ub', $lock.AI_MODEL_UBATCH_SIZE,
    '-n', $maxOutputTokens, '--threads-http', '2', '--poll', '0',
    '--poll-batch', '0', '--cache-type-k', 'q4_0', '--cache-type-v', 'q4_0',
    '--host', '127.0.0.1', '--port', $lock.AI_MODEL_PORT, '--no-webui',
    '--reasoning', $reasoningFormat, '--reasoning-budget', $reasoningBudget
)
$process = Start-Process -FilePath $server -ArgumentList $args -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
$process.Id | Set-Content (Join-Path $state 'llama-server-windows.pid') -Encoding ascii
for ($attempt = 0; $attempt -lt 120; $attempt++) {
    Start-Sleep -Seconds 2
    if ($process.HasExited) { throw "llama-server stopped during startup. See $stderr" }
    try {
        Invoke-RestMethod -TimeoutSec 2 "$baseUrl/v1/models" | Out-Null
        Write-Host "LOCAL_AI_READY_WINDOWS profile=$Profile $baseUrl pid=$($process.Id)"
        return
    } catch {}
}
throw "llama-server did not become ready. See $stderr"
