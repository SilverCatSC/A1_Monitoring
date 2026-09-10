# Shared helpers: native exit codes must be checked explicitly in PowerShell.
function Invoke-A1Native {
    param([string]$Executable, [string[]]$ArgumentList)
    & $Executable @ArgumentList | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "$Executable failed (exit $LASTEXITCODE). Setup stopped; see output above."
    }
}

function Get-A1BaseUrl {
    param([string]$ProjectRoot)
    $port = '18000'
    foreach ($line in (Get-Content -LiteralPath (Join-Path $ProjectRoot '.env') -Encoding utf8)) {
        if ($line -match '^\s*APP_BIND_PORT\s*=(.*)$') {
            $port = $Matches[1].Trim().Trim('"').Trim("'")
        }
    }
    if ($port -notmatch '^\d{1,5}$' -or [int]$port -lt 1 -or [int]$port -gt 65535) {
        throw 'Invalid APP_BIND_PORT in .env.'
    }
    return "http://127.0.0.1:$port"
}

function Get-A1LockValues {
    param([string]$ProjectRoot)
    $values = @{}
    $path = Join-Path $ProjectRoot 'config\ai-tools.lock'
    foreach ($rawLine in (Get-Content -LiteralPath $path -Encoding utf8 -ErrorAction Stop)) {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith('#') -or -not $line.Contains('=')) { continue }
        $name, $value = $line.Split('=', 2)
        $values[$name.Trim()] = $value.Trim()
    }
    return $values
}

function Write-A1MemorySnapshot {
    param([string]$Stage)
    $os = Get-CimInstance Win32_OperatingSystem
    $freeMb = [math]::Round($os.FreePhysicalMemory / 1024)
    $totalMb = [math]::Round($os.TotalVisibleMemorySize / 1024)
    Write-Host "MEMORY stage=$Stage free_mb=$freeMb total_mb=$totalMb"
    return $freeMb
}
