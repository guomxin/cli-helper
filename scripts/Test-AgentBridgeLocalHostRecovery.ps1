[CmdletBinding()]
param(
    [string]$LegacyGatewayTaskName = "OpenClaw Gateway",
    [string]$GuardTaskName = "AgentBridge OpenClaw Guard",
    [string]$TunnelTaskName = "AgentBridge Workspace Tunnel",
    [ValidateRange(5, 300)][int]$TimeoutSeconds = 180,
    [ValidateRange(1, 65535)][int]$GatewayPort = 18789,
    [switch]$ExerciseFailureRecovery,
    [switch]$ExerciseTunnelRestart
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$tunnelInstaller = Join-Path $PSScriptRoot "Install-AgentBridgeWorkspaceTunnel.ps1"

function Wait-Until {
    param(
        [Parameter(Mandatory = $true)][scriptblock]$Condition,
        [Parameter(Mandatory = $true)][string]$Description
    )
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    $lastError = $null
    do {
        try {
            $value = & $Condition
            if ($value) { return $value }
        }
        catch {
            $lastError = $_.Exception.Message
        }
        Start-Sleep -Seconds 2
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    $suffix = if ($lastError) { "; last error: $lastError" } else { "" }
    throw "Timed out waiting for $Description$suffix"
}

function Get-GatewayListener {
    $listeners = @(
        Get-NetTCPConnection -State Listen -LocalPort $GatewayPort -ErrorAction SilentlyContinue |
            Sort-Object OwningProcess -Unique
    )
    if ($listeners.Count -ne 1) { return $null }
    return $listeners[0]
}

function Get-GatewayReadyState {
    $listener = Get-GatewayListener
    if (-not $listener) { return $null }
    try {
        $ready = Invoke-RestMethod `
            -Uri "http://127.0.0.1:$GatewayPort/readyz" `
            -TimeoutSec 5 `
            -Method Get
        if ([bool]$ready.ready -and @($ready.failing).Count -eq 0) {
            return [ordered]@{
                listener = $listener
                readiness = $ready
            }
        }
    }
    catch {
        return $null
    }
    return $null
}

function Get-TunnelReadyStatus {
    $path = Join-Path $env:LOCALAPPDATA "AgentBridge\workspace-tunnel-status.json"
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $null }
    try {
        $status = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
        $age = [DateTimeOffset]::UtcNow -
            [DateTimeOffset]::Parse([string]$status.observedAt)
        if ($status.state -ne "connected" -or $age.TotalSeconds -gt 45 -or
            -not (Get-Process -Id ([int]$status.processId) -ErrorAction SilentlyContinue) -or
            -not (Get-Process -Id ([int]$status.sshProcessId) -ErrorAction SilentlyContinue)) {
            return $null
        }
        if ($status.businessCalls -ne 0 -or
            $status.businessListReads -ne 0 -or
            $status.businessWrites -ne 0) {
            throw "Workspace tunnel crossed its zero-business boundary"
        }
        return [ordered]@{
            status = $status
            ageSeconds = [Math]::Round($age.TotalSeconds, 1)
        }
    }
    catch {
        return $null
    }
}

function Get-LatestAgentBridgePluginRegistration {
    param([DateTimeOffset]$NotBefore)

    $logRoot = Join-Path ([IO.Path]::GetTempPath()) "openclaw"
    $logFiles = @(
        Get-ChildItem -LiteralPath $logRoot -Filter "openclaw-*.log" -File `
            -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending
    )
    $registrations = @()
    foreach ($logFile in $logFiles) {
        foreach ($raw in @(Get-Content -LiteralPath $logFile.FullName -Tail 4000)) {
            try { $entry = $raw | ConvertFrom-Json } catch { continue }
            if (-not $entry.PSObject.Properties["message"] -or
                -not $entry.PSObject.Properties["time"]) {
                continue
            }
            if ($entry.message -notmatch
                'AgentBridge interaction plugin registered \(version=([^,\)]+)') {
                continue
            }
            $version = $Matches[1]
            $registeredAt = [DateTimeOffset]::Parse([string]$entry.time)
            if ($registeredAt -lt $NotBefore) { continue }
            $registrations += [pscustomobject]@{
                registeredAt = $registeredAt.ToString("o")
                version = $version
            }
        }
        if ($registrations.Count -gt 0) { break }
    }
    return $registrations |
        Sort-Object registeredAt |
        Select-Object -Last 1
}

$tunnelTask = Get-ScheduledTask -TaskName $TunnelTaskName -ErrorAction Stop
$guardStatusPath = Join-Path $env:LOCALAPPDATA "AgentBridge\openclaw-guard-status.json"
if (-not (Test-Path -LiteralPath $guardStatusPath -PathType Leaf)) {
    throw "OpenClaw startup guard status is absent"
}
$guardStatus = Get-Content -LiteralPath $guardStatusPath -Raw | ConvertFrom-Json
$guardAge = [DateTimeOffset]::UtcNow - [DateTimeOffset]::Parse($guardStatus.observedAt)
if ($guardAge.TotalSeconds -gt 90) { throw "OpenClaw startup guard heartbeat is stale" }
if ($guardStatus.businessCalls -ne 0 -or
    $guardStatus.businessListReads -ne 0 -or
    $guardStatus.businessWrites -ne 0) {
    throw "OpenClaw startup guard crossed its zero-business boundary"
}
if (-not $guardStatus.gatewayVisibleForeground) {
    throw "OpenClaw startup guard does not require a visible Gateway"
}
if (-not (Get-Process -Id $guardStatus.processId -ErrorAction SilentlyContinue)) {
    throw "OpenClaw startup guard process is not running"
}
$guardTask = Get-ScheduledTask -TaskName $GuardTaskName -ErrorAction Stop
if ($guardTask.State -ne "Running") {
    throw "OpenClaw guard scheduled task is not running"
}
if ($guardTask.Settings.RestartCount -lt 1 -or
    [string]$guardTask.Settings.ExecutionTimeLimit -ne "PT0S" -or
    $guardTask.Settings.MultipleInstances.ToString() -ne "IgnoreNew") {
    throw "OpenClaw guard scheduled task is missing its recovery policy"
}
$legacyGatewayTask = Get-ScheduledTask `
    -TaskName $LegacyGatewayTaskName `
    -ErrorAction SilentlyContinue
$gatewayTaskLauncher = Join-Path $env:USERPROFILE ".openclaw\gateway.cmd"
if (-not (Test-Path -LiteralPath $gatewayTaskLauncher -PathType Leaf) -or
    (Get-Content -LiteralPath $gatewayTaskLauncher -Raw) -notmatch
        'AgentBridge visible Gateway task shim') {
    throw "OpenClaw Gateway task does not use the visible lifecycle shim"
}
if ($tunnelTask.Settings.RestartCount -lt 1) { throw "Tunnel task has no restart policy" }
if ([string]$tunnelTask.Settings.ExecutionTimeLimit -ne "PT0S") {
    throw "Tunnel task still has an execution time limit"
}
if (-not $tunnelTask.Settings.StartWhenAvailable) {
    throw "Tunnel task is not configured to start when available"
}

$beforeState = Wait-Until -Description "one ready OpenClaw Gateway" -Condition {
    Get-GatewayReadyState
}
$tunnelReady = Wait-Until -Description "one connected Workspace tunnel" -Condition {
    Get-TunnelReadyStatus
}
$before = $beforeState.listener
$pluginNotBefore = [DateTimeOffset]::MinValue
$failureRecovery = $null
if ($ExerciseFailureRecovery) {
    $pluginNotBefore = [DateTimeOffset]::Now
    $oldPid = [int]$before.OwningProcess
    Stop-Process -Id $oldPid -Force
    $afterState = Wait-Until -Description "guarded visible Gateway failure recovery" -Condition {
        $candidate = Get-GatewayReadyState
        if ($candidate -and [int]$candidate.listener.OwningProcess -ne $oldPid) {
            $candidate
        }
    }
    $after = $afterState.listener
    $failureRecovery = [ordered]@{
        exercised = $true
        previousPid = $oldPid
        recoveredPid = [int]$after.OwningProcess
        recovered = $true
    }
}

if ($ExerciseTunnelRestart) {
    $previousTunnelPid = [int]$tunnelReady.status.processId
    & $tunnelInstaller | Out-Null
    $tunnelReady = Wait-Until -Description "Workspace tunnel task restart" -Condition {
        $candidate = Get-TunnelReadyStatus
        if ($candidate -and
            [int]$candidate.status.processId -ne $previousTunnelPid) {
            $candidate
        }
    }
}

$readyState = Wait-Until -Description "one ready OpenClaw Gateway" -Condition {
    Get-GatewayReadyState
}
$listener = $readyState.listener
$plugin = Wait-Until -Description "AgentBridge plugin registration log" -Condition {
    Get-LatestAgentBridgePluginRegistration -NotBefore $pluginNotBefore
}

$tunnelTask = Get-ScheduledTask -TaskName $TunnelTaskName
$guardTask = Get-ScheduledTask -TaskName $GuardTaskName
if ($guardTask.State -ne "Running") { throw "OpenClaw guard task is not running" }
if ($tunnelTask.State -ne "Running") { throw "Workspace tunnel task is not running" }

[ordered]@{
    status = "succeeded"
    checkedAt = [DateTimeOffset]::UtcNow.ToString("o")
    gateway = [ordered]@{
        selfHealMode = "startup_guard_visible_gateway"
        guardTaskState = $guardTask.State.ToString()
        legacyTaskState = if ($legacyGatewayTask) {
            $legacyGatewayTask.State.ToString()
        } else {
            "absent"
        }
        listenerPid = [int]$listener.OwningProcess
        listenerCount = 1
        visibleForeground = $true
        readyEndpoint = "ok"
        pluginStatus = "loaded"
        pluginVersion = $plugin.version
        guardObservedAt = $guardStatus.observedAt
    }
    tunnel = [ordered]@{
        taskState = $tunnelTask.State.ToString()
        restartPolicy = $tunnelTask.Settings.RestartCount
        restartExercised = [bool]$ExerciseTunnelRestart
        state = [string]$tunnelReady.status.state
        wrapperProcessId = [int]$tunnelReady.status.processId
        sshProcessId = [int]$tunnelReady.status.sshProcessId
        statusAgeSeconds = $tunnelReady.ageSeconds
        resumeGapThresholdSeconds = [int]$tunnelReady.status.resumeGapThresholdSeconds
        statusHeartbeatSeconds = [int]$tunnelReady.status.statusHeartbeatSeconds
    }
    failureRecovery = $failureRecovery
    businessCalls = 0
    businessListReads = 0
    businessWrites = 0
} | ConvertTo-Json -Compress -Depth 8
