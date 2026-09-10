[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'windows_common.ps1')
$lock = Get-A1LockValues $Root
$modelDir = Join-Path $Root 'artifacts\models'
New-Item -ItemType Directory -Force $modelDir | Out-Null

foreach ($name in @($lock.AI_MODEL_FILE, $lock.AI_MODEL_MMPROJ_FILE)) {
    $target = Join-Path $modelDir $name
    if ((Test-Path $target) -and (Get-Item $target).Length -gt 0) {
        Write-Host "MODEL_COMPONENT_EXISTS file=$target"
        continue
    }
    $url = "https://huggingface.co/$($lock.AI_MODEL_REPO)/resolve/main/$name?download=true"
    Write-Host "Downloading $name. Do not close the terminal."
    Invoke-A1Native 'curl.exe' @('-L', '--fail', '--retry', '3', '--continue-at', '-', '--output', $target, $url)
    if (-not (Test-Path $target) -or (Get-Item $target).Length -eq 0) { throw "Empty model component: $target" }
    (Get-FileHash -Algorithm SHA256 $target).Hash.ToLowerInvariant() | Set-Content "$target.sha256" -Encoding ascii
}
Write-Host "LOCAL_MULTIMODAL_MODEL_READY_WINDOWS dir=$modelDir"
