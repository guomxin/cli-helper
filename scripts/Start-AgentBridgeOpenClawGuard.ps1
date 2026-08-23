[CmdletBinding()]
param(
    [string]$GatewayTaskName = "OpenClaw Gateway",
    [string]$TunnelTaskName = "AgentBridge Workspace Tunnel",
    [ValidateRange(5, 300)][int]$IntervalSeconds = 15,
    [ValidateRange(1, 65535)][int]$GatewayPort = 18789,
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

try {
    do {
        $gatewayAction = "none"
        $tunnelAction = "none"
        $lastError = $null
        try {
            if (-not (Test-GatewayListener)) {
                Start-ScheduledTask -TaskName $GatewayTaskName
                $gatewayAction = "start_requested"
                Write-GuardLog "Gateway listener missing; scheduled task start requested."
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
