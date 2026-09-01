[CmdletBinding()]
param(
    [string]$HostName = "10.10.50.213",
    [string]$SshUser = "root",
    [string]$IdentityFile = "$env:USERPROFILE\.ssh\id_ed25519_10_10_50_213",
    [string]$KnownHostsFile = "",
    [int]$LocalPort = 18789,
    [int]$RemotePort = 18789,
    [int]$ReconnectDelaySeconds = 5,
    [int]$NetworkPollSeconds = 2,
    [int]$ResumeGapThresholdSeconds = 60,
    [int]$StatusHeartbeatSeconds = 15,
    [switch]$Once
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $KnownHostsFile) {
    $KnownHostsFile = Join-Path $repoRoot "deploy\ssh\agentbridge_known_hosts"
}
if ($HostName -notmatch '^[A-Za-z0-9.-]+$') { throw "HostName contains unsupported characters" }
if ($SshUser -notmatch '^[A-Za-z0-9._-]+$') { throw "SshUser contains unsupported characters" }
if ($LocalPort -lt 1 -or $LocalPort -gt 65535) { throw "LocalPort is invalid" }
if ($RemotePort -lt 1 -or $RemotePort -gt 65535) { throw "RemotePort is invalid" }
if ($ReconnectDelaySeconds -lt 1 -or $ReconnectDelaySeconds -gt 300) {
    throw "ReconnectDelaySeconds is invalid"
}
if ($NetworkPollSeconds -lt 1 -or $NetworkPollSeconds -gt 30) {
    throw "NetworkPollSeconds is invalid"
}
if ($ResumeGapThresholdSeconds -lt 5 -or $ResumeGapThresholdSeconds -gt 300) {
    throw "ResumeGapThresholdSeconds is invalid"
}
if ($StatusHeartbeatSeconds -lt 5 -or $StatusHeartbeatSeconds -gt 300) {
    throw "StatusHeartbeatSeconds is invalid"
}
foreach ($path in @($IdentityFile, $KnownHostsFile)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required tunnel file was not found: $path"
    }
}

$mutexName = "Local\AgentBridgeWorkspaceTunnel-{0}-{1}-{2}-{3}" -f (
    $env:USERNAME,
    ($HostName -replace '[^A-Za-z0-9]', '_'),
    $RemotePort,
    $LocalPort
)
$createdNew = $false
$mutex = [Threading.Mutex]::new($true, $mutexName, [ref]$createdNew)
if (-not $createdNew) { exit 0 }

$stateRoot = Join-Path $env:LOCALAPPDATA "AgentBridge"
$logRoot = Join-Path $stateRoot "logs"
$statusPath = Join-Path $stateRoot "workspace-tunnel-status.json"
$statusTempPath = "$statusPath.tmp"
$sshErrorPath = Join-Path $logRoot "workspace-tunnel-ssh.log"
$sshAttemptErrorPath = Join-Path $logRoot "workspace-tunnel-ssh-$PID.attempt.log"
$consecutiveFailures = 0
$lastConnectedAt = $null
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null

function Write-TunnelStatus {
    param(
        [Parameter(Mandatory = $true)][string]$State,
        [Nullable[int]]$SshProcessId,
        [Nullable[int]]$ExitCode = $null,
        [string]$Reason = ""
    )
    $status = [ordered]@{
        schemaVersion = "agentbridge.workspace-tunnel.v1"
        processId = $PID
        observedAt = [DateTimeOffset]::UtcNow.ToString("o")
        state = $State
        sshProcessId = $SshProcessId
        exitCode = $ExitCode
        hostName = $HostName
        localPort = $LocalPort
        remotePort = $RemotePort
        reason = $Reason
        resumeGapThresholdSeconds = $ResumeGapThresholdSeconds
        statusHeartbeatSeconds = $StatusHeartbeatSeconds
        consecutiveFailures = $consecutiveFailures
        lastConnectedAt = $lastConnectedAt
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
}

function Complete-SshAttemptLog {
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [Nullable[int]]$ExitCode = $null
    )

    $detail = ""
    if (Test-Path -LiteralPath $sshAttemptErrorPath -PathType Leaf) {
        $detail = (Get-Content -LiteralPath $sshAttemptErrorPath -Raw).Trim()
    }
    $reason = if ($detail -match
        'remote port forwarding failed|Address already in use|cannot listen to port') {
        "remote_forward_conflict"
    }
    elseif ($detail -match 'Permission denied|Authentication failed') {
        "authentication_failed"
    }
    elseif ($detail -match
        'Connection timed out|Connection refused|No route to host|Network is unreachable') {
        "network_unreachable"
    }
    else {
        "ssh_exited"
    }

    if ((Test-Path -LiteralPath $sshErrorPath) -and
        (Get-Item -LiteralPath $sshErrorPath).Length -gt 1MB) {
        Move-Item -LiteralPath $sshErrorPath `
            -Destination (Join-Path $logRoot "workspace-tunnel-ssh.1.log") `
            -Force
    }
    $header = "{0} pid={1} exit={2} reason={3}" -f (
        [DateTimeOffset]::UtcNow.ToString("o"),
        $ProcessId,
        $(if ($null -eq $ExitCode) { "unknown" } else { $ExitCode }),
        $reason
    )
    Add-Content -LiteralPath $sshErrorPath -Value $header -Encoding utf8
    if ($detail) {
        Add-Content -LiteralPath $sshErrorPath -Value $detail -Encoding utf8
    }
    if (Test-Path -LiteralPath $sshAttemptErrorPath) {
        Remove-Item -LiteralPath $sshAttemptErrorPath -Force
    }
    return $reason
}

function Get-NetworkFingerprint {
    $entries = @(
        [Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces() |
            Where-Object {
                $_.OperationalStatus -eq [Net.NetworkInformation.OperationalStatus]::Up -and
                $_.NetworkInterfaceType -ne [Net.NetworkInformation.NetworkInterfaceType]::Loopback
            } |
            ForEach-Object {
                try {
                    $properties = $_.GetIPProperties()
                    $addresses = @(
                        $properties.UnicastAddresses |
                            Where-Object {
                                $_.Address.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetwork -and
                                $_.Address.ToString() -notlike "169.254.*"
                            } |
                            ForEach-Object { $_.Address.ToString() } |
                            Sort-Object
                    )
                    if ($addresses.Count -gt 0) {
                        $gateways = @(
                            $properties.GatewayAddresses |
                                Where-Object {
                                    $_.Address.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetwork -and
                                    $_.Address.ToString() -ne "0.0.0.0"
                                } |
                                ForEach-Object { $_.Address.ToString() } |
                                Sort-Object
                        )
                        "{0}|{1}|{2}" -f $_.Id, ($addresses -join ","), ($gateways -join ",")
                    }
                }
                catch {
                    # Ignore an adapter that disappears while the snapshot is collected.
                }
            } |
            Sort-Object
    )
    return $entries -join ";"
}

function Get-ExistingTunnelProcess {
    $forwardMarker = "-R 127.0.0.1:${RemotePort}:127.0.0.1:${LocalPort}"
    $targetMarker = "${SshUser}@${HostName}"
    return Get-CimInstance Win32_Process -Filter "Name = 'ssh.exe'" `
        -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -like "*$forwardMarker*" -and
            $_.CommandLine -like "*$targetMarker*"
        } |
        Sort-Object CreationDate |
        Select-Object -First 1
}

$ssh = Get-Command ssh.exe -ErrorAction SilentlyContinue
if (-not $ssh) { $ssh = Get-Command ssh -ErrorAction Stop }
$arguments = @(
    "-N", "-T",
    "-o", "BatchMode=yes",
    "-o", "ExitOnForwardFailure=yes",
    "-o", "ServerAliveInterval=10",
    "-o", "ServerAliveCountMax=2",
    "-o", "ConnectTimeout=10",
    "-o", "IdentitiesOnly=yes",
    "-o", "UserKnownHostsFile=$((Resolve-Path $KnownHostsFile).Path)",
    "-i", (Resolve-Path $IdentityFile).Path,
    "-R", "127.0.0.1:${RemotePort}:127.0.0.1:${LocalPort}",
    "${SshUser}@${HostName}"
)

try {
    do {
        $existing = Get-ExistingTunnelProcess
        if ($existing) {
            Write-TunnelStatus -State "existing_tunnel_observed" `
                -SshProcessId $existing.ProcessId
            if ($Once) { exit 0 }
            Wait-Process -Id $existing.ProcessId -ErrorAction SilentlyContinue
            Start-Sleep -Seconds $ReconnectDelaySeconds
            continue
        }

        if (Test-Path -LiteralPath $sshAttemptErrorPath) {
            Remove-Item -LiteralPath $sshAttemptErrorPath -Force
        }
        $sshProcess = Start-Process `
            -FilePath $ssh.Source `
            -ArgumentList $arguments `
            -NoNewWindow `
            -RedirectStandardError $sshAttemptErrorPath `
            -PassThru
        Write-TunnelStatus -State "connection_started" `
            -SshProcessId $sshProcess.Id
        $networkFingerprint = Get-NetworkFingerprint
        $connectedReported = $false
        $networkChanged = $false
        $resumeDetected = $false
        $lastPollAt = [DateTimeOffset]::UtcNow
        $lastStatusHeartbeatAt = $lastPollAt
        while (-not $sshProcess.WaitForExit($NetworkPollSeconds * 1000)) {
            $observedAt = [DateTimeOffset]::UtcNow
            $pollGapSeconds = ($observedAt - $lastPollAt).TotalSeconds
            $lastPollAt = $observedAt
            if ($pollGapSeconds -gt $ResumeGapThresholdSeconds) {
                $resumeDetected = $true
                Write-TunnelStatus -State "resume_detected" `
                    -SshProcessId $sshProcess.Id `
                    -Reason "system_resume_or_long_pause"
                Stop-Process -Id $sshProcess.Id -Force -ErrorAction SilentlyContinue
                if (-not $sshProcess.HasExited) {
                    $sshProcess.WaitForExit()
                }
                break
            }
            $currentFingerprint = Get-NetworkFingerprint
            if ($currentFingerprint -ne $networkFingerprint) {
                $networkChanged = $true
                Write-TunnelStatus -State "network_change_detected" `
                    -SshProcessId $sshProcess.Id `
                    -Reason "active_network_changed"
                Stop-Process -Id $sshProcess.Id -Force -ErrorAction SilentlyContinue
                if (-not $sshProcess.HasExited) {
                    $sshProcess.WaitForExit()
                }
                break
            }
            if (-not $connectedReported) {
                $consecutiveFailures = 0
                $lastConnectedAt = [DateTimeOffset]::UtcNow.ToString("o")
                Write-TunnelStatus -State "connected" `
                    -SshProcessId $sshProcess.Id
                $connectedReported = $true
                $lastStatusHeartbeatAt = [DateTimeOffset]::UtcNow
            }
            elseif (($observedAt - $lastStatusHeartbeatAt).TotalSeconds -ge
                $StatusHeartbeatSeconds) {
                Write-TunnelStatus -State "connected" `
                    -SshProcessId $sshProcess.Id
                $lastStatusHeartbeatAt = $observedAt
            }
        }
        $sshProcess.WaitForExit()
        $exitCode = [int]$sshProcess.ExitCode
        $sshExitReason = Complete-SshAttemptLog `
            -ProcessId $sshProcess.Id `
            -ExitCode $exitCode
        $consecutiveFailures++
        $exitReason = if ($resumeDetected) {
            "system_resume_or_long_pause"
        }
        elseif ($networkChanged) {
            "active_network_changed"
        }
        else {
            $sshExitReason
        }
        Write-TunnelStatus -State "connection_exited" `
            -SshProcessId $sshProcess.Id `
            -ExitCode $exitCode `
            -Reason $exitReason
        if ($Once) { exit $exitCode }
        $delaySeconds = if ($networkChanged -or $resumeDetected) {
            [Math]::Min(2, $ReconnectDelaySeconds)
        }
        else {
            $ReconnectDelaySeconds
        }
        Start-Sleep -Seconds $delaySeconds
    } while ($true)
}
finally {
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
