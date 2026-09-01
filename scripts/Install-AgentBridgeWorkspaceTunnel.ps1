[CmdletBinding()]
param(
    [string]$TaskName = "AgentBridge Workspace Tunnel",
    [switch]$NoStart
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$tunnelScript = (Resolve-Path (Join-Path $PSScriptRoot "Start-AgentBridgeWorkspaceTunnel.ps1")).Path
$powershell = (Get-Command powershell.exe -ErrorAction Stop).Source
$action = New-ScheduledTaskAction `
    -Execute $powershell `
    -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $tunnelScript)
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
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
    -Description "Keeps AgentBridge Workspace connected to the local OpenClaw Gateway across IP and network changes."
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
$wasRunning = $existing -and $existing.State -eq "Running"
if ($wasRunning) {
    Stop-ScheduledTask -TaskName $TaskName
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(30)
    do {
        Start-Sleep -Milliseconds 500
        $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    } while ($existing -and $existing.State -eq "Running" -and
        [DateTimeOffset]::UtcNow -lt $deadline)
}

$scriptMarker = "-File `"$tunnelScript`""
Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" `
    -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*$scriptMarker*" } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }

Register-ScheduledTask `
    -TaskName $TaskName `
    -InputObject $task `
    -Force `
    -ErrorAction Stop | Out-Null
if (-not $NoStart) {

    $forwardMarker = "-R 127.0.0.1:18789:127.0.0.1:18789"
    Get-CimInstance Win32_Process -Filter "Name = 'ssh.exe'" `
        -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -like "*$forwardMarker*" -and
            $_.CommandLine -like "*root@10.10.50.213*"
        } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
    Start-ScheduledTask -TaskName $TaskName
}

[ordered]@{
    status = "installed"
    taskName = $TaskName
    started = -not $NoStart
    script = $tunnelScript
} | ConvertTo-Json -Compress
