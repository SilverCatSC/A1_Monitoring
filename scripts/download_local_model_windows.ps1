[CmdletBinding()]
param([ValidateSet('light','heavy')][string]$Profile = 'light')

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'windows_common.ps1')
$lock = Get-A1LockValues $Root
$modelDir = Join-Path $Root 'artifacts\models'
New-Item -ItemType Directory -Force $modelDir | Out-Null
$modelRepo = $lock.AI_MODEL_REPO
$modelFile = $lock.AI_MODEL_FILE
$mmprojFile = $lock.AI_MODEL_MMPROJ_FILE
if ($Profile -eq 'heavy') {
    $modelRepo = $lock.AI_HEAVY_MODEL_REPO
    $modelFile = $lock.AI_HEAVY_MODEL_FILE
    $mmprojFile = $lock.AI_HEAVY_MODEL_MMPROJ_FILE
}

foreach ($name in @($modelFile, $mmprojFile)) {
    $target = Join-Path $modelDir $name
    if ((Test-Path $target) -and (Get-Item $target).Length -gt 0) {
        Write-Host "MODEL_COMPONENT_EXISTS file=$target"
        continue
    }
    $url = "https://huggingface.co/$modelRepo/resolve/main/${name}?download=true"
    Write-Host "Downloading $name. Do not close the terminal."
    Invoke-A1Native 'curl.exe' @('-L', '--fail', '--retry', '3', '--continue-at', '-', '--output', $target, $url)
    if (-not (Test-Path $target) -or (Get-Item $target).Length -eq 0) { throw "Empty model component: $target" }
    (Get-FileHash -Algorithm SHA256 $target).Hash.ToLowerInvariant() | Set-Content "$target.sha256" -Encoding ascii
}
Write-Host "LOCAL_MULTIMODAL_MODEL_READY_WINDOWS profile=$Profile dir=$modelDir"
