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
$mode = "scheduled_task"
$nativeTaskUpdated = $false
try {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -InputObject $task `
        -Force `
        -ErrorAction Stop | Out-Null
    $nativeTaskUpdated = $true
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
}
catch {
    if ($_.Exception.Message -notmatch "Access is denied|拒绝访问") { throw }
    $mode = "startup_guard"
    $guardScript = (Resolve-Path (
        Join-Path $PSScriptRoot "Start-AgentBridgeOpenClawGuard.ps1"
    )).Path
    $startup = [Environment]::GetFolderPath("Startup")
    if (-not $startup) { throw "Windows Startup folder was not found" }
    $guardLauncher = Join-Path $startup "AgentBridge-OpenClaw-Guard.cmd"
    $launcherContent = @(
        "@echo off",
        ('start "" /min powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $guardScript)
    ) -join "`r`n"
    [IO.File]::WriteAllText(
        $guardLauncher,
        "$launcherContent`r`n",
        [Text.UTF8Encoding]::new($false)
    )
    if (-not $NoStart) {
        Start-Process `
            -FilePath powershell.exe `
            -ArgumentList @(
                "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-WindowStyle", "Hidden", "-File", $guardScript
            ) `
            -WindowStyle Hidden
    }
}

$installed = Get-ScheduledTask -TaskName $TaskName
[ordered]@{
    status = "installed"
    mode = $mode
    nativeTaskUpdated = $nativeTaskUpdated
    taskName = $TaskName
    started = -not $NoStart
    launcher = $resolvedLauncher
    startupDelaySeconds = $StartupDelaySeconds
    restartCount = $installed.Settings.RestartCount
    restartInterval = [string]$installed.Settings.RestartInterval
    executionTimeLimit = [string]$installed.Settings.ExecutionTimeLimit
    multipleInstances = $installed.Settings.MultipleInstances.ToString()
    businessCalls = 0
    businessListReads = 0
    businessWrites = 0
} | ConvertTo-Json -Compress
