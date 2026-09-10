[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $Root
. (Join-Path $PSScriptRoot 'windows_common.ps1')
$lock = Get-A1LockValues $Root

$HermesHome = Join-Path $Root 'artifacts\hermes_home'
$HermesInstall = Join-Path $Root 'artifacts\hermes_agent'
$installer = Join-Path $env:TEMP 'a1-hermes-install.ps1'
Invoke-WebRequest -UseBasicParsing -Uri 'https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.ps1' -OutFile $installer
& pwsh.exe -NoProfile -ExecutionPolicy Bypass -File $installer -HermesHome $HermesHome -InstallDir $HermesInstall -Commit $lock.HERMES_COMMIT -ForceCommit -SkipSetup -SkipComputerUse -NonInteractive
if ($LASTEXITCODE -ne 0) { throw "Hermes installer failed (exit $LASTEXITCODE)." }

$uvCandidates = @(
    (Join-Path $HermesHome 'bin\uv.exe'),
    (Join-Path $HermesInstall 'venv\Scripts\uv.exe'),
    (Join-Path $env:LOCALAPPDATA 'hermes\bin\uv.exe')
)
$uv = $uvCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $uv) {
    $uvCommand = Get-Command uv -ErrorAction SilentlyContinue
    if ($uvCommand) { $uv = $uvCommand.Source }
}
if (-not $uv) { throw 'uv.exe not found after Hermes installation.' }

$env:UV_TOOL_DIR = Join-Path $Root 'artifacts\ai_tools\tools'
$env:UV_TOOL_BIN_DIR = Join-Path $Root 'artifacts\ai_tools\bin'
New-Item -ItemType Directory -Force $env:UV_TOOL_DIR, $env:UV_TOOL_BIN_DIR | Out-Null
Invoke-A1Native $uv @('tool', 'install', '--python', '3.12', '--force', "ouroboros-ai[mcp]==$($lock.OUROBOROS_VERSION)")

& (Join-Path $PSScriptRoot 'sync_ai_profiles_windows.ps1')
Invoke-A1Native (Join-Path $HermesInstall 'venv\Scripts\python.exe') @((Join-Path $HermesInstall 'hermes'), '--version')
Invoke-A1Native (Join-Path $env:UV_TOOL_BIN_DIR 'ouroboros.exe') @('--version')
& (Join-Path $PSScriptRoot 'install_llama_cpp_windows.ps1')
Write-Host 'AI_TOOLS_INSTALLED_WINDOWS'
Write-Host 'Next: .\scripts\download_local_model_windows.ps1'
