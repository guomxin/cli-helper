[CmdletBinding()]
param(
    [string]$TaskName = "OpenClaw Gateway",
    [string]$GuardTaskName = "AgentBridge OpenClaw Guard",
    [string]$GatewayTaskLauncher = "$env:USERPROFILE\.openclaw\gateway.cmd",
    [string]$GatewayRuntimeLauncher = (
        "$env:LOCALAPPDATA\AgentBridge\openclaw-gateway-runtime.cmd"
    ),
    [switch]$NoStart
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $GatewayTaskLauncher -PathType Leaf)) {
    throw "OpenClaw Gateway task launcher was not found: $GatewayTaskLauncher"
}

$guardScript = (Resolve-Path (
    Join-Path $PSScriptRoot "Start-AgentBridgeOpenClawGuard.ps1"
)).Path
$lifecycleScript = (Resolve-Path (
    Join-Path $PSScriptRoot "Restart-AgentBridgeOpenClawGateway.ps1"
)).Path
$foregroundScript = (Resolve-Path (
    Join-Path $PSScriptRoot "Invoke-AgentBridgeOpenClawGatewayForeground.ps1"
)).Path

$stateRoot = Split-Path -Parent $GatewayRuntimeLauncher
New-Item -ItemType Directory -Path $stateRoot -Force | Out-Null
$guardLauncher = Join-Path $stateRoot "openclaw-guard-hidden.vbs"
$guardStatusPath = Join-Path $stateRoot "openclaw-guard-status.json"

function Stop-ExistingGuardProcesses {
    $escapedGuardScript = [regex]::Escape($guardScript)
    $stopped = @()
    foreach ($process in @(
        Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" `
            -ErrorAction SilentlyContinue |
            Where-Object {
                $_.CommandLine -match 'Start-AgentBridgeOpenClawGuard\.ps1' -and
                $_.CommandLine -match $escapedGuardScript
            }
    )) {
        Stop-Process -Id ([int]$process.ProcessId) -Force -ErrorAction Stop
        $stopped += [int]$process.ProcessId
    }
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(10)
    foreach ($processId in $stopped) {
        while ((Get-Process -Id $processId -ErrorAction SilentlyContinue) -and
            [DateTimeOffset]::UtcNow -lt $deadline) {
            Start-Sleep -Milliseconds 200
        }
        if (Get-Process -Id $processId -ErrorAction SilentlyContinue) {
            throw "Previous OpenClaw Guard process did not stop: $processId"
        }
    }
    return $stopped
}
$taskLauncherContent = Get-Content -LiteralPath $GatewayTaskLauncher -Raw
$shimMarker = "AgentBridge visible Gateway task shim"
if ($taskLauncherContent -notmatch [regex]::Escape($shimMarker)) {
    [IO.File]::WriteAllText(
        $GatewayRuntimeLauncher,
        $taskLauncherContent,
        [Text.UTF8Encoding]::new($false)
    )
}
elseif (-not (Test-Path -LiteralPath $GatewayRuntimeLauncher -PathType Leaf)) {
    throw "Gateway task launcher is already shimmed but the runtime launcher is absent"
}

$taskShim = @(
    "@echo off",
    "rem $shimMarker",
    (
        'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}" -StartOnly -GatewayLauncher "{1}"' -f `
            $lifecycleScript, $GatewayRuntimeLauncher
    )
) -join "`r`n"
[IO.File]::WriteAllText(
    $GatewayTaskLauncher,
    "$taskShim`r`n",
    [Text.UTF8Encoding]::new($false)
)

$guardCommand = (
    'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f `
        $guardScript
)
$escapedGuardCommand = $guardCommand.Replace('"', '""')
$guardLauncherContent = @(
    'Set shell = CreateObject("WScript.Shell")',
    ('exitCode = shell.Run("{0}", 0, True)' -f $escapedGuardCommand),
    'WScript.Quit exitCode'
) -join "`r`n"
[IO.File]::WriteAllText(
    $guardLauncher,
    "$guardLauncherContent`r`n",
    [Text.UTF8Encoding]::new($false)
)

$guardAction = New-ScheduledTaskAction `
    -Execute "$env:SystemRoot\System32\wscript.exe" `
    -Argument ('"{0}"' -f $guardLauncher)
$guardTrigger = New-ScheduledTaskTrigger `
    -AtLogOn `
    -User "$env:USERDOMAIN\$env:USERNAME"
$guardSettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew
$guardPrincipal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited
$guardTask = New-ScheduledTask `
    -Action $guardAction `
    -Trigger $guardTrigger `
    -Settings $guardSettings `
    -Principal $guardPrincipal `
    -Description "Keeps one visible AgentBridge OpenClaw Gateway available."

$existingGuardTask = Get-ScheduledTask `
    -TaskName $GuardTaskName `
    -ErrorAction SilentlyContinue
if ($existingGuardTask -and $existingGuardTask.State -eq "Running") {
    Stop-ScheduledTask -TaskName $GuardTaskName
    Start-Sleep -Seconds 1
}
$stoppedGuardProcessIds = @(Stop-ExistingGuardProcesses)
Remove-Item -LiteralPath $guardStatusPath -Force -ErrorAction SilentlyContinue
Register-ScheduledTask `
    -TaskName $GuardTaskName `
    -InputObject $guardTask `
    -Force | Out-Null

$startup = [Environment]::GetFolderPath("Startup")
if ($startup) {
    $retiredStartupLauncher = Join-Path $startup "AgentBridge-OpenClaw-Guard.cmd"
    Remove-Item -LiteralPath $retiredStartupLauncher `
        -Force `
        -ErrorAction SilentlyContinue
}

if (-not $NoStart) {
    Start-ScheduledTask -TaskName $GuardTaskName
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(30)
    $guardReady = $false
    do {
        Start-Sleep -Milliseconds 250
        $candidateTask = Get-ScheduledTask `
            -TaskName $GuardTaskName `
            -ErrorAction SilentlyContinue
        if (-not $candidateTask -or $candidateTask.State -ne "Running" -or
            -not (Test-Path -LiteralPath $guardStatusPath -PathType Leaf)) {
            continue
        }
        try {
            $candidateStatus = Get-Content `
                -LiteralPath $guardStatusPath `
                -Raw | ConvertFrom-Json
            $statusProcess = Get-Process `
                -Id ([int]$candidateStatus.processId) `
                -ErrorAction SilentlyContinue
            if ($statusProcess -and
                $candidateStatus.PSObject.Properties["lifecycleLeaseState"]) {
                $guardReady = $true
            }
        }
        catch {
            $guardReady = $false
        }
    } while (-not $guardReady -and [DateTimeOffset]::UtcNow -lt $deadline)
    if (-not $guardReady) {
        throw "OpenClaw Guard did not publish a current lifecycle-aware heartbeat"
    }
}

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
$installedGuardTask = Get-ScheduledTask -TaskName $GuardTaskName
[ordered]@{
    status = "installed"
    mode = "startup_guard_visible_gateway"
    taskName = $TaskName
    taskState = if ($task) { $task.State.ToString() } else { "absent" }
    guardTaskName = $GuardTaskName
    guardTaskState = $installedGuardTask.State.ToString()
    stoppedGuardProcessIds = $stoppedGuardProcessIds
    taskShimInstalled = $true
    started = -not $NoStart
    gatewayTaskLauncher = (Resolve-Path -LiteralPath $GatewayTaskLauncher).Path
    gatewayRuntimeLauncher = (Resolve-Path -LiteralPath $GatewayRuntimeLauncher).Path
    guardLauncher = $guardLauncher
    guardScript = $guardScript
    lifecycleScript = $lifecycleScript
    foregroundScript = $foregroundScript
    startupLauncherRetired = $true
    guardConsoleHidden = $true
    visibleForeground = $true
    businessCalls = 0
    businessListReads = 0
    businessWrites = 0
} | ConvertTo-Json -Compress
