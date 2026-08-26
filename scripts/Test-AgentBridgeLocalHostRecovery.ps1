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

function Get-LatestAgentBridgePluginRegistration {
    param([DateTimeOffset]$NotBefore)

    $logPath = Join-Path (
        Join-Path ([IO.Path]::GetTempPath()) "openclaw"
    ) ("openclaw-{0}.log" -f [DateTime]::Now.ToString("yyyy-MM-dd"))
    if (-not (Test-Path -LiteralPath $logPath -PathType Leaf)) { return $null }
    $registrations = @()
    foreach ($raw in @(Get-Content -LiteralPath $logPath -Tail 600)) {
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
    return $registrations | Select-Object -Last 1
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
    Stop-ScheduledTask -TaskName $TunnelTaskName
    Start-Sleep -Seconds 2
    Start-ScheduledTask -TaskName $TunnelTaskName
    Wait-Until -Description "Workspace tunnel task restart" -Condition {
        $task = Get-ScheduledTask -TaskName $TunnelTaskName
        if ($task.State -eq "Running") { $task }
    } | Out-Null
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
    }
    failureRecovery = $failureRecovery
    businessCalls = 0
    businessListReads = 0
    businessWrites = 0
} | ConvertTo-Json -Compress -Depth 8
