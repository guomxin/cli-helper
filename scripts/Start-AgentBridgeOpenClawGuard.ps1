[CmdletBinding()]
param(
    [string]$GatewayTaskName = "OpenClaw Gateway",
    [string]$TunnelTaskName = "AgentBridge Workspace Tunnel",
    [ValidateRange(5, 300)][int]$IntervalSeconds = 15,
    [ValidateRange(1, 65535)][int]$GatewayPort = 18789,
    [ValidateRange(30, 1800)][int]$GatewayStartupGraceSeconds = 600,
    [switch]$Once
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$createdNew = $false
$mutex = [Threading.Mutex]::new(
    $true,
    "Local\AgentBridgeOpenClawGuard-$env:USERNAME",
    [ref]$createdNew
)
if (-not $createdNew) { exit 0 }

$stateRoot = Join-Path $env:LOCALAPPDATA "AgentBridge"
$logRoot = Join-Path $stateRoot "logs"
$statusPath = Join-Path $stateRoot "openclaw-guard-status.json"
$statusTempPath = "$statusPath.tmp"
$logPath = Join-Path $logRoot "openclaw-guard.log"
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null

function Write-GuardLog {
    param([Parameter(Mandatory = $true)][string]$Message)
    if ((Test-Path -LiteralPath $logPath) -and
        (Get-Item -LiteralPath $logPath).Length -gt 1MB) {
        $archivePath = Join-Path $logRoot "openclaw-guard.1.log"
        Move-Item -LiteralPath $logPath -Destination $archivePath -Force
    }
    $line = "{0} {1}" -f [DateTimeOffset]::UtcNow.ToString("o"), $Message
    Add-Content -LiteralPath $logPath -Value $line -Encoding utf8
}

function Test-GatewayListener {
    return @(
        Get-NetTCPConnection -State Listen -LocalPort $GatewayPort `
            -ErrorAction SilentlyContinue
    ).Count -gt 0
}

function Get-GatewayProcesses {
    return @(
        Get-CimInstance Win32_Process -Filter "Name = 'node.exe'" `
            -ErrorAction SilentlyContinue |
            Where-Object {
                $_.CommandLine -match 'openclaw\.mjs.*gateway run' -and
                $_.CommandLine -match "--port\s+$GatewayPort(?:\s|$)"
            }
    )
}

function Stop-GatewayProcess {
    param([Parameter(Mandatory = $true)]$Process)

    $current = Get-CimInstance Win32_Process `
        -Filter "ProcessId = $($Process.ProcessId)" `
        -ErrorAction SilentlyContinue
    if (-not $current -or $current.Name -ne "node.exe" -or
        $current.CommandLine -notmatch 'openclaw\.mjs.*gateway run' -or
        $current.CommandLine -notmatch "--port\s+$GatewayPort(?:\s|$)") {
        return $false
    }
    Stop-Process -Id $current.ProcessId -Force -ErrorAction Stop
    return $true
}

try {
    do {
        $gatewayAction = "none"
        $tunnelAction = "none"
        $lastError = $null
        try {
            $listener = @(
                Get-NetTCPConnection -State Listen -LocalPort $GatewayPort `
                    -ErrorAction SilentlyContinue
            ) | Select-Object -First 1
            $gatewayProcesses = @(Get-GatewayProcesses)

            if ($listener) {
                $duplicates = @(
                    $gatewayProcesses |
                        Where-Object { $_.ProcessId -ne $listener.OwningProcess }
                )
                foreach ($duplicate in $duplicates) {
                    if (Stop-GatewayProcess -Process $duplicate) {
                        Write-GuardLog (
                            "Stopped duplicate Gateway process {0}; listener owner is {1}." -f `
                                $duplicate.ProcessId, $listener.OwningProcess
                        )
                    }
                }
                if ($duplicates.Count -gt 0) {
                    $gatewayAction = "duplicates_removed"
                }
            }
            elseif ($gatewayProcesses.Count -gt 0) {
                $newest = $gatewayProcesses |
                    Sort-Object CreationDate -Descending |
                    Select-Object -First 1
                $duplicates = @(
                    $gatewayProcesses |
                        Where-Object { $_.ProcessId -ne $newest.ProcessId }
                )
                foreach ($duplicate in $duplicates) {
                    if (Stop-GatewayProcess -Process $duplicate) {
                        Write-GuardLog (
                            "Stopped duplicate starting Gateway process {0}; preserving {1}." -f `
                                $duplicate.ProcessId, $newest.ProcessId
                        )
                    }
                }
                $ageSeconds = ([DateTime]::Now - [DateTime]$newest.CreationDate).TotalSeconds
                if ($ageSeconds -lt $GatewayStartupGraceSeconds) {
                    $gatewayAction = "startup_in_progress"
                }
                else {
                    Stop-ScheduledTask -TaskName $GatewayTaskName -ErrorAction SilentlyContinue
                    if (Stop-GatewayProcess -Process $newest) {
                        Write-GuardLog (
                            "Stopped Gateway process {0} after startup grace expired ({1:N0}s)." -f `
                                $newest.ProcessId, $ageSeconds
                        )
                    }
                    Start-ScheduledTask -TaskName $GatewayTaskName
                    $gatewayAction = "stale_start_replaced"
                    Write-GuardLog "Gateway startup grace expired; scheduled task restarted once."
                }
            }
            else {
                Start-ScheduledTask -TaskName $GatewayTaskName
                $gatewayAction = "start_requested"
                Write-GuardLog "Gateway process and listener missing; scheduled task start requested."
            }
            $tunnel = Get-ScheduledTask -TaskName $TunnelTaskName -ErrorAction Stop
            if ($tunnel.State -ne "Running") {
                Start-ScheduledTask -TaskName $TunnelTaskName
                $tunnelAction = "start_requested"
                Write-GuardLog "Workspace tunnel was not running; scheduled task start requested."
            }
        }
        catch {
            $lastError = $_.Exception.GetType().Name
            Write-GuardLog "Guard cycle failed: $lastError"
        }
        $status = [ordered]@{
            schemaVersion = "agentbridge.openclaw-guard.v1"
            processId = $PID
            observedAt = [DateTimeOffset]::UtcNow.ToString("o")
            gatewayTaskName = $GatewayTaskName
            tunnelTaskName = $TunnelTaskName
            gatewayListening = Test-GatewayListener
            gatewayProcessCount = @(Get-GatewayProcesses).Count
            gatewayStartupGraceSeconds = $GatewayStartupGraceSeconds
            gatewayAction = $gatewayAction
            tunnelAction = $tunnelAction
            errorCode = $lastError
            businessCalls = 0
            businessListReads = 0
            businessWrites = 0
        }
        [IO.File]::WriteAllText(
            $statusTempPath,
            ($status | ConvertTo-Json -Compress),
            [Text.UTF8Encoding]::new($false)
        )
        Move-Item -LiteralPath $statusTempPath -Destination $statusPath -Force
        if (-not $Once) { Start-Sleep -Seconds $IntervalSeconds }
    } while (-not $Once)
}
finally {
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
