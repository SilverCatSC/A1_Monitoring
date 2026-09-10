[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pairs = @(
    @('config\hermes-monitoring.yaml', 'artifacts\hermes_home\config.yaml'),
    @('config\hermes-maintenance.yaml', 'artifacts\hermes_maintenance_home\config.yaml'),
    @('config\hermes-ouroboros-llm.yaml', 'artifacts\hermes_ouroboros_llm_home\config.yaml')
)
foreach ($pair in $pairs) {
    $target = Join-Path $Root $pair[1]
    New-Item -ItemType Directory -Force (Split-Path $target) | Out-Null
    Copy-Item -Force (Join-Path $Root $pair[0]) $target
}
$ouroborosHome = Join-Path $Root 'artifacts\ouroboros_home'
$ouroborosConfig = Join-Path $ouroborosHome '.ouroboros\config.yaml'
New-Item -ItemType Directory -Force (Split-Path $ouroborosConfig) | Out-Null
$portableRoot = $Root.Replace('\', '/')
(Get-Content (Join-Path $Root 'config\ouroboros-windows.yaml.template') -Raw -Encoding utf8).Replace('__PROJECT_ROOT__', $portableRoot) |
    Set-Content -LiteralPath $ouroborosConfig -Encoding utf8
Write-Host 'AI_PROFILES_SYNCED_WINDOWS'
