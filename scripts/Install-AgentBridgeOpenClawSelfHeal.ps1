[CmdletBinding()]
param(
    [string]$TaskName = "OpenClaw Gateway",
    [string]$GatewayLauncher = "$env:USERPROFILE\.openclaw\gateway.cmd",
    [ValidateRange(0, 300)][int]$StartupDelaySeconds = 20,
    [switch]$NoStart
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $GatewayLauncher -PathType Leaf)) {
    throw "OpenClaw Gateway launcher was not found: $GatewayLauncher"
}
$resolvedLauncher = (Resolve-Path -LiteralPath $GatewayLauncher).Path
$action = New-ScheduledTaskAction -Execute $resolvedLauncher
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
if ($StartupDelaySeconds -gt 0) {
    $trigger.Delay = "PT${StartupDelaySeconds}S"
}
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited
$task = New-ScheduledTask `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Keeps the local OpenClaw Gateway available for AgentBridge clients after sign-in and process failures."

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
$wasRunning = $existing -and $existing.State -eq "Running"
Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null
if (-not $NoStart) {
    if ($wasRunning) {
        Stop-ScheduledTask -TaskName $TaskName
        $deadline = [DateTimeOffset]::UtcNow.AddSeconds(30)
        do {
            Start-Sleep -Milliseconds 500
            $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        } while ($existing -and $existing.State -eq "Running" -and [DateTimeOffset]::UtcNow -lt $deadline)
    }
    Start-ScheduledTask -TaskName $TaskName
}

$installed = Get-ScheduledTask -TaskName $TaskName
[ordered]@{
    status = "installed"
    taskName = $TaskName
    started = -not $NoStart
    launcher = $resolvedLauncher
    startupDelaySeconds = $StartupDelaySeconds
    restartCount = $installed.Settings.RestartCount
    restartInterval = $installed.Settings.RestartInterval.ToString()
    executionTimeLimit = $installed.Settings.ExecutionTimeLimit.ToString()
    multipleInstances = $installed.Settings.MultipleInstances.ToString()
    businessCalls = 0
    businessListReads = 0
    businessWrites = 0
} | ConvertTo-Json -Compress
