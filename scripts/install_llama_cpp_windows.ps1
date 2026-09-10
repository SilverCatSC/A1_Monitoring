[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$runtime = Join-Path $Root 'artifacts\llama_cpp_windows'
$server = Join-Path $runtime 'llama-server.exe'
if (Test-Path $server) { Write-Host "LLAMA_CPP_EXISTS $server"; return }

$releases = Invoke-RestMethod -Headers @{ 'User-Agent' = 'A1-Monitoring-Setup' } -Uri 'https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=20'
$release = $null
$asset = $null
foreach ($candidate in $releases) {
    $candidateAsset = $candidate.assets | Where-Object { $_.name -match '^llama-b\d+-bin-win-cpu-x64\.zip$' } | Select-Object -First 1
    if ($candidateAsset) { $release = $candidate; $asset = $candidateAsset; break }
}
if (-not $asset) { throw 'Official llama.cpp Windows x64 CPU archive not found in the 20 newest releases.' }
$archive = Join-Path $env:TEMP $asset.name
Invoke-WebRequest -UseBasicParsing -Uri $asset.browser_download_url -OutFile $archive
New-Item -ItemType Directory -Force $runtime | Out-Null
Expand-Archive -LiteralPath $archive -DestinationPath $runtime -Force
if (-not (Test-Path $server)) {
    $found = Get-ChildItem $runtime -Recurse -Filter llama-server.exe | Select-Object -First 1
    if (-not $found) { throw 'llama-server.exe missing after archive extraction.' }
    Copy-Item $found.FullName $server
    Get-ChildItem $found.Directory.FullName -File | Where-Object Name -ne 'llama-server.exe' | Copy-Item -Destination $runtime -Force
}
@{ tag = $release.tag_name; asset = $asset.name; sha256 = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLowerInvariant() } |
    ConvertTo-Json | Set-Content (Join-Path $runtime 'installed_release.json') -Encoding utf8
Write-Host "LLAMA_CPP_INSTALLED_WINDOWS version=$($release.tag_name) file=$server"
