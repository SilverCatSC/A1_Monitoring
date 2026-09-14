[CmdletBinding()]
param(
    [ValidatePattern('^([01][0-9]|2[0-3]):[0-5][0-9]$')]
    [string]$At = '09:00',
    [ValidateSet('auto_ru,avito', 'auto_ru', 'avito')]
    [string]$Engines = 'auto_ru,avito',
    [ValidateRange(1, 10)]
    [int]$Pages = 3,
    [switch]$Apply
)

# Registration is intentionally a plan by default.  A scheduled host runner is
# allowed only in the current signed-in desktop session, never as Session 0.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RunnerScript = Join-Path $PSScriptRoot 'run_monitoring_host_windows.ps1'
$TaskPath = '\A1Monitoring\'
$TaskName = 'InteractiveCycle'

function Get-A1InteractiveTaskIdentity {
    $currentProcess = Get-Process -Id $PID -ErrorAction Stop
    if ([int]$currentProcess.SessionId -eq 0) {
        throw 'Task registration is refused in Session 0. Run it from the signed-in Windows desktop.'
    }

    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not $identity -or -not $identity.User -or $identity.User.Value -eq 'S-1-5-18') {
        throw 'Task registration requires a signed-in interactive Windows user.'
    }

    $desktopShell = Get-Process -Name explorer -ErrorAction SilentlyContinue |
        Where-Object { $_.SessionId -eq $currentProcess.SessionId } |
        Select-Object -First 1
    if (-not $desktopShell) {
        throw 'Task registration requires an active interactive Explorer desktop.'
    }
    return $identity
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
    throw 'Windows PowerShell was not found.'
}

function Quote-A1TaskArgument {
    param([string]$Value)

    # Windows file names cannot contain a double quote. Quoting the script path
    # protects normal project folders such as C:\work\A1 Monitoring.
    return ('"{0}"' -f $Value)
}

function Ensure-A1TaskFolder {
    param([string]$Path)

    # The ScheduledTasks cmdlets do not create a missing task folder. Do this
    # only after -Apply so plan-only mode remains entirely non-mutating.
    $serviceType = [Type]::GetTypeFromProgID('Schedule.Service')
    if (-not $serviceType) {
        throw 'Windows Task Scheduler COM service is unavailable.'
    }
    $service = [Activator]::CreateInstance($serviceType)
    $service.Connect()
    try {
        [void]$service.GetFolder($Path)
        return
    } catch {
        # Create only the fixed A1Monitoring child under the scheduler root.
        # Any access failure is reported rather than silently using another path.
    }

    try {
        $rootFolder = $service.GetFolder('\')
        try {
            [void]$rootFolder.CreateFolder('A1Monitoring', $null)
        } catch {
            # Another administrator may have created the fixed folder between
            # GetFolder and CreateFolder. Validate it below before proceeding.
        }
        [void]$service.GetFolder($Path)
    } catch {
        throw 'Cannot create or access the A1Monitoring Task Scheduler folder.'
    }
}

if (-not (Test-Path -LiteralPath $RunnerScript)) {
    throw 'The interactive host runner script is missing.'
}
$identity = Get-A1InteractiveTaskIdentity
$time = [DateTime]::ParseExact($At, 'HH:mm', [Globalization.CultureInfo]::InvariantCulture)
$firstRun = (Get-Date).Date.AddDays(1).AddHours($time.Hour).AddMinutes($time.Minute)
$powershell = Get-A1WindowsPowerShell
$arguments = '-NoLogo -NoProfile -ExecutionPolicy Bypass -File ' + (Quote-A1TaskArgument $RunnerScript) +
    " -Engines $Engines -Pages $Pages"

$plan = [ordered]@{
    mode = if ($Apply) { 'apply' } else { 'plan_only' }
    task_path = $TaskPath
    task_name = $TaskName
    principal = 'current_interactive_user'
    logon_type = 'Interactive'
    run_level = 'Limited'
    session_0 = 'never'
    cadence = 'daily'
    first_run_local = $firstRun.ToString('yyyy-MM-ddTHH:mm:ssK')
    multiple_instances = 'IgnoreNew'
    automatic_retry = 'disabled'
    invocation = 'one_cautious_cycle'
    registration_starts_marketplace_scan = $false
}

if (-not $Apply) {
    Write-Host 'TASK_REGISTRATION_PLAN_ONLY: pass -Apply to register or update the task.'
    $plan | ConvertTo-Json -Depth 3
    exit 0
}

foreach ($commandName in @(
    'New-ScheduledTaskAction', 'New-ScheduledTaskTrigger', 'New-ScheduledTaskSettingsSet',
    'New-ScheduledTaskPrincipal', 'Register-ScheduledTask'
)) {
    if (-not (Get-Command $commandName -ErrorAction SilentlyContinue)) {
        throw "ScheduledTasks cmdlet is unavailable: $commandName"
    }
}

Ensure-A1TaskFolder -Path $TaskPath
$action = New-ScheduledTaskAction -Execute $powershell -Argument $arguments
$trigger = New-ScheduledTaskTrigger -Daily -At $firstRun
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 0
$principal = New-ScheduledTaskPrincipal -UserId $identity.Name -LogonType Interactive -RunLevel Limited

# Register-ScheduledTask is the only mutation in this script. The first trigger
# is deliberately tomorrow, so registration itself cannot launch a scan.
Register-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Description 'A1 Monitoring: one cautious visible-Chrome cycle for the interactive user.' `
    -Force | Out-Null

Write-Host 'TASK_REGISTRATION_APPLIED: no marketplace scan was started.'
$plan | ConvertTo-Json -Depth 3
