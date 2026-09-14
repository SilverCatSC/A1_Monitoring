[CmdletBinding()]
param(
    [ValidateSet('auto_ru,avito', 'auto_ru', 'avito')]
    [string]$Engines = 'auto_ru,avito',
    [ValidateRange(1, 10)]
    [int]$Pages = 3
)

# This runner is deliberately host-side: the visible Chrome session must belong
# to the signed-in Windows user, never to a Session 0 service account.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $Root
. (Join-Path $PSScriptRoot 'windows_common.ps1')

$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
$script:RunnerStartedAt = [DateTime]::UtcNow.ToString('o')
$script:RunnerPhase = 'startup'
$script:FinalStatusWritten = $false
$script:StatusPath = Join-Path $Root 'artifacts\monitoring_host_runner_status.json'
$script:ScanExitCode = -1
$Pace = 'cautious'

function Get-A1UtcTimestamp {
    return [DateTime]::UtcNow.ToString('o')
}

function Write-A1RunnerStatus {
    param(
        [ValidateSet('starting', 'running', 'succeeded', 'partial', 'failed', 'interrupted')]
        [string]$State,
        [string]$Phase,
        [int]$ScanExitCode = -1,
        [switch]$Finished,
        [bool]$RecoveredAbandonedMutex = $false
    )

    # Keep the status intentionally small and operational only.  In particular,
    # do not put exception text, URLs, profile paths, cookies, or credentials here.
    $payload = [ordered]@{
        schema_version = 1
        runner = 'windows_interactive_host'
        execution_model = 'one_cycle_per_invocation'
        state = $State
        phase = $Phase
        started_at_utc = $script:RunnerStartedAt
        updated_at_utc = Get-A1UtcTimestamp
        engines = $Engines
        pages = $Pages
        pace = $Pace
    }
    if ($ScanExitCode -ge 0) {
        $payload.scan_exit_code = $ScanExitCode
    }
    if ($RecoveredAbandonedMutex) {
        $payload.recovered_abandoned_mutex = $true
    }
    if ($Finished) {
        $payload.finished_at_utc = Get-A1UtcTimestamp
    }

    $statusDirectory = Split-Path -Parent $script:StatusPath
    New-Item -ItemType Directory -Path $statusDirectory -Force | Out-Null
    $temporaryPath = Join-Path $statusDirectory (
        '.monitoring_host_runner_status.{0}.tmp' -f [Guid]::NewGuid().ToString('N')
    )
    $encoding = [System.Text.UTF8Encoding]::new($false)
    try {
        [System.IO.File]::WriteAllText(
            $temporaryPath,
            (($payload | ConvertTo-Json -Compress -Depth 4) + [Environment]::NewLine),
            $encoding
        )

        # Both paths are in the same directory. File.Replace/Move therefore
        # publishes either the old complete JSON or the new complete JSON.
        if ([System.IO.File]::Exists($script:StatusPath)) {
            [System.IO.File]::Replace($temporaryPath, $script:StatusPath, $null)
        } else {
            [System.IO.File]::Move($temporaryPath, $script:StatusPath)
        }
    } finally {
        if (Test-Path -LiteralPath $temporaryPath) {
            Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
        }
    }
    Write-Host "HOST_RUNNER_STATUS state=$State phase=$Phase"
}

function Assert-A1InteractiveDesktop {
    $currentProcess = Get-Process -Id $PID -ErrorAction Stop
    if ([int]$currentProcess.SessionId -eq 0) {
        throw 'Refusing to run monitoring in Session 0. Use a signed-in interactive Windows desktop.'
    }

    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not $identity -or -not $identity.User -or $identity.User.Value -eq 'S-1-5-18') {
        throw 'Refusing to run monitoring under a service identity. Use a signed-in interactive Windows user.'
    }

    $desktopShell = Get-Process -Name explorer -ErrorAction SilentlyContinue |
        Where-Object { $_.SessionId -eq $currentProcess.SessionId } |
        Select-Object -First 1
    if (-not $desktopShell) {
        throw 'No interactive Explorer desktop is available in this session; visible Chrome must not run headlessly.'
    }
}

function Get-A1MutexName {
    param([string]$ProjectRoot)

    $normalizedRoot = $ProjectRoot.TrimEnd('\', '/').ToLowerInvariant()
    $hash = [Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [Text.Encoding]::UTF8.GetBytes($normalizedRoot)
        $hex = ([BitConverter]::ToString($hash.ComputeHash($bytes))).Replace('-', '')
    } finally {
        $hash.Dispose()
    }
    return ('Global\A1MonitoringHostRunner_{0}' -f $hex.Substring(0, 16))
}

function Get-A1WindowsPowerShell {
    if ($env:WINDIR) {
        $candidate = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }
    $command = Get-Command 'powershell.exe' -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Path
    }
    throw 'Windows PowerShell was not found; cannot isolate the local scan child process.'
}

Assert-A1InteractiveDesktop
$mutex = $null
$lockTaken = $false
$recoveredAbandonedMutex = $false
$exitCode = 1

try {
    $mutex = [System.Threading.Mutex]::new($false, (Get-A1MutexName $Root))
    try {
        $lockTaken = $mutex.WaitOne(0)
    } catch [System.Threading.AbandonedMutexException] {
        # The old runner disappeared. The safe recovery CLI below records any
        # open ledger row before this invocation is allowed to scan.
        $lockTaken = $true
        $recoveredAbandonedMutex = $true
    }

    if (-not $lockTaken) {
        Write-Host 'HOST_RUNNER_SKIPPED_ACTIVE'
        $exitCode = 0
    } else {
        Write-A1RunnerStatus -State 'starting' -Phase 'preflight' -RecoveredAbandonedMutex $recoveredAbandonedMutex
        $Python = Join-Path $Root '.venv312\Scripts\python.exe'
        if (-not (Test-Path -LiteralPath $Python)) {
            throw 'Python environment is missing. Run scripts/setup_windows.ps1 in the interactive user session.'
        }
        if (-not (Get-Command 'docker' -ErrorAction SilentlyContinue)) {
            throw 'Docker Desktop is not available in the interactive user session.'
        }

        $script:RunnerPhase = 'services'
        Write-A1RunnerStatus -State 'running' -Phase $script:RunnerPhase -RecoveredAbandonedMutex $recoveredAbandonedMutex
        Invoke-A1Native 'docker' @('compose', 'up', '-d', 'app', 'db', 'backup')

        $script:RunnerPhase = 'readiness'
        Write-A1RunnerStatus -State 'running' -Phase $script:RunnerPhase -RecoveredAbandonedMutex $recoveredAbandonedMutex
        Invoke-A1Native $Python @('scripts/doctor.py', '--http', '--wait')

        $script:RunnerPhase = 'recover_open_cycles'
        Write-A1RunnerStatus -State 'running' -Phase $script:RunnerPhase -RecoveredAbandonedMutex $recoveredAbandonedMutex
        Invoke-A1Native $Python @('-m', 'app.cli', 'recover-open-cycles')

        $script:RunnerPhase = 'scan'
        Write-A1RunnerStatus -State 'running' -Phase $script:RunnerPhase -RecoveredAbandonedMutex $recoveredAbandonedMutex
        $scanScript = Join-Path $PSScriptRoot 'local_scan_windows.ps1'
        $powershell = Get-A1WindowsPowerShell
        & $powershell '-NoLogo' '-NoProfile' '-ExecutionPolicy' 'Bypass' '-File' $scanScript `
            '-Engines' $Engines '-Pages' $Pages '-Pace' $Pace
        $script:ScanExitCode = [int]$LASTEXITCODE

        if ($script:ScanExitCode -eq 0) {
            Write-A1RunnerStatus -State 'succeeded' -Phase 'scan_finished' -ScanExitCode 0 -Finished `
                -RecoveredAbandonedMutex $recoveredAbandonedMutex
            $script:FinalStatusWritten = $true
            Write-Host 'HOST_RUNNER_OK'
            $exitCode = 0
        } elseif ($script:ScanExitCode -eq 2) {
            # A partial result is evidence for review, not a reason to immediately
            # repeat marketplace traffic. The next scheduled invocation decides anew.
            Write-A1RunnerStatus -State 'partial' -Phase 'scan_finished' -ScanExitCode 2 -Finished `
                -RecoveredAbandonedMutex $recoveredAbandonedMutex
            $script:FinalStatusWritten = $true
            Write-Warning 'HOST_RUNNER_PARTIAL: no automatic retry was started.'
            $exitCode = 2
        } else {
            Write-A1RunnerStatus -State 'failed' -Phase 'scan_finished' -ScanExitCode $script:ScanExitCode -Finished `
                -RecoveredAbandonedMutex $recoveredAbandonedMutex
            $script:FinalStatusWritten = $true
            Write-Warning 'HOST_RUNNER_FAILED: local scan did not complete.'
            $exitCode = 1
        }
    }
} catch {
    if ($lockTaken -and -not $script:FinalStatusWritten) {
        try {
            Write-A1RunnerStatus -State 'failed' -Phase $script:RunnerPhase -ScanExitCode $script:ScanExitCode -Finished `
                -RecoveredAbandonedMutex $recoveredAbandonedMutex
            $script:FinalStatusWritten = $true
        } catch {
            Write-Warning 'HOST_RUNNER_STATUS_WRITE_FAILED'
        }
    }
    # Do not print the raw exception: errors from environment, browser, or Docker
    # may contain private operational details. The current phase is enough to triage.
    Write-Warning "HOST_RUNNER_FAILED phase=$script:RunnerPhase"
    $exitCode = 1
} finally {
    if ($lockTaken -and -not $script:FinalStatusWritten) {
        try {
            Write-A1RunnerStatus -State 'interrupted' -Phase $script:RunnerPhase -ScanExitCode $script:ScanExitCode -Finished `
                -RecoveredAbandonedMutex $recoveredAbandonedMutex
        } catch {
            Write-Warning 'HOST_RUNNER_STATUS_WRITE_FAILED'
        }
    }
    if ($mutex) {
        if ($lockTaken) {
            $mutex.ReleaseMutex()
        }
        $mutex.Dispose()
    }
}

exit $exitCode
