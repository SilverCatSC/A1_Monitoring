[CmdletBinding()]
param(
    [string]$BaseUrl = 'http://127.0.0.1:18000',
    [string]$Out = 'public',
    [Parameter(Mandatory=$true)] [string]$Allowlist,
    [string]$AuthEnv = '',
    [switch]$Push,
    [string]$CommitMessage = 'Publish monitoring report'
)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $Root
$Python = Join-Path $Root '.venv312\Scripts\python.exe'
if (-not (Test-Path $Python)) { throw 'Окружение не найдено. Выполните .\scripts\setup_windows.ps1.' }
$exportArgs = @('scripts/export_public_report.py', '--base-url', $BaseUrl, '--out', $Out, '--allowlist', $Allowlist)
if ($AuthEnv) { $exportArgs += @('--auth-env', $AuthEnv) }
& $Python @exportArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if ($Push) {
    & git add -f -- $Out
    & git diff --cached --check
    & git commit -m $CommitMessage
    & git push
    if ($LASTEXITCODE -ne 0) { throw 'Git push завершился ошибкой.' }
    Write-Host 'PAGES_PUBLISHED_REQUESTED'
} else {
    Write-Host 'PAGES_EXPORT_READY: публикация не выполнялась. Для явной отправки используйте -Push.'
}
