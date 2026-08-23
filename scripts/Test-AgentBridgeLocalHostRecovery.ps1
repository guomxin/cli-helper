[CmdletBinding()]
param(
    [string]$GatewayTaskName = "OpenClaw Gateway",
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
    do {
        try {
            $value = & $Condition
            if ($value) { return $value }
        }
        catch {
            # Transient errors are expected while the process is restarting.
        }
        Start-Sleep -Seconds 2
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw "Timed out waiting for $Description"
}

function Get-GatewayListener {
    $listeners = @(
        Get-NetTCPConnection -State Listen -LocalPort $GatewayPort -ErrorAction SilentlyContinue |
            Sort-Object OwningProcess -Unique
    )
    if ($listeners.Count -ne 1) { return $null }
    return $listeners[0]
}

function Invoke-OpenClawJson {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Label
    )
    $command = Get-Command openclaw.cmd -ErrorAction SilentlyContinue
    if (-not $command) { $command = Get-Command openclaw -ErrorAction Stop }
    $nonce = [Guid]::NewGuid().ToString("N")
    $stdoutPath = Join-Path ([IO.Path]::GetTempPath()) "agentbridge-host-$nonce.out"
    $stderrPath = Join-Path ([IO.Path]::GetTempPath()) "agentbridge-host-$nonce.err"
    try {
        $process = Start-Process `
            -FilePath $command.Source `
            -ArgumentList $Arguments `
            -WindowStyle Hidden `
            -RedirectStandardOutput $stdoutPath `
            -RedirectStandardError $stderrPath `
            -PassThru
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            $process.WaitForExit()
            throw "$Label timed out"
        }
        $stdout = [IO.File]::ReadAllText($stdoutPath).Trim()
        $stderr = [IO.File]::ReadAllText($stderrPath).Trim()
        if ($process.ExitCode -ne 0 -or -not $stdout) {
            $detail = if ($stderr) { $stderr } else { $stdout }
            throw "$Label failed: $detail"
        }
        return $stdout | ConvertFrom-Json
    }
    finally {
        Remove-Item -LiteralPath $stdoutPath, $stderrPath -Force -ErrorAction SilentlyContinue
    }
}

$gatewayTask = Get-ScheduledTask -TaskName $GatewayTaskName -ErrorAction Stop
$tunnelTask = Get-ScheduledTask -TaskName $TunnelTaskName -ErrorAction Stop
if ($gatewayTask.Settings.RestartCount -lt 1) { throw "Gateway task has no restart policy" }
if ([string]$gatewayTask.Settings.ExecutionTimeLimit -ne "PT0S") {
    throw "Gateway task still has an execution time limit"
}
if (-not $gatewayTask.Settings.StartWhenAvailable) {
    throw "Gateway task is not configured to start when available"
}
if ($gatewayTask.Settings.MultipleInstances.ToString() -ne "IgnoreNew") {
    throw "Gateway task does not reject duplicate instances"
}
if ($tunnelTask.Settings.RestartCount -lt 1) { throw "Tunnel task has no restart policy" }
if ([string]$tunnelTask.Settings.ExecutionTimeLimit -ne "PT0S") {
    throw "Tunnel task still has an execution time limit"
}
if (-not $tunnelTask.Settings.StartWhenAvailable) {
    throw "Tunnel task is not configured to start when available"
}

$before = Wait-Until -Description "one OpenClaw Gateway listener" -Condition { Get-GatewayListener }
$failureRecovery = $null
if ($ExerciseFailureRecovery) {
    $oldPid = [int]$before.OwningProcess
    Stop-Process -Id $oldPid -Force
    $after = Wait-Until -Description "scheduled Gateway failure recovery" -Condition {
        $candidate = Get-GatewayListener
        if ($candidate -and [int]$candidate.OwningProcess -ne $oldPid) { $candidate }
    }
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

$listener = Wait-Until -Description "one OpenClaw Gateway listener" -Condition { Get-GatewayListener }
$gateway = Invoke-OpenClawJson `
    -Arguments @("gateway", "status", "--deep", "--require-rpc", "--json") `
    -Label "OpenClaw Gateway deep RPC check"
$plugin = Invoke-OpenClawJson `
    -Arguments @("plugins", "inspect", "agentbridge-interactions", "--json") `
    -Label "AgentBridge plugin check"
if (-not $gateway.rpc.ok) { throw "OpenClaw Gateway RPC is unavailable" }
if ($plugin.plugin.status -ne "loaded") { throw "AgentBridge plugin is not loaded" }

$gatewayTask = Get-ScheduledTask -TaskName $GatewayTaskName
$tunnelTask = Get-ScheduledTask -TaskName $TunnelTaskName
if ($gatewayTask.State -ne "Running") { throw "Gateway task is not running" }
if ($tunnelTask.State -ne "Running") { throw "Workspace tunnel task is not running" }

[ordered]@{
    status = "succeeded"
    checkedAt = [DateTimeOffset]::UtcNow.ToString("o")
    gateway = [ordered]@{
        taskState = $gatewayTask.State.ToString()
        listenerPid = [int]$listener.OwningProcess
        listenerCount = 1
        rpc = "ok"
        cliVersion = $gateway.cli.version
        gatewayVersion = $gateway.gateway.version
        pluginStatus = $plugin.plugin.status
        pluginVersion = $plugin.plugin.version
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
