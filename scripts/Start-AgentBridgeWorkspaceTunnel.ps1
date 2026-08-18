[CmdletBinding()]
param(
    [string]$HostName = "10.10.50.213",
    [string]$SshUser = "root",
    [string]$IdentityFile = "$env:USERPROFILE\.ssh\id_ed25519_10_10_50_213",
    [string]$KnownHostsFile = "",
    [int]$LocalPort = 18789,
    [int]$RemotePort = 18789,
    [int]$ReconnectDelaySeconds = 5,
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
foreach ($path in @($IdentityFile, $KnownHostsFile)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required tunnel file was not found: $path"
    }
}

$ssh = Get-Command ssh.exe -ErrorAction SilentlyContinue
if (-not $ssh) { $ssh = Get-Command ssh -ErrorAction Stop }
$arguments = @(
    "-N", "-T",
    "-o", "BatchMode=yes",
    "-o", "ExitOnForwardFailure=yes",
    "-o", "ServerAliveInterval=30",
    "-o", "ServerAliveCountMax=3",
    "-o", "ConnectTimeout=10",
    "-o", "IdentitiesOnly=yes",
    "-o", "UserKnownHostsFile=$((Resolve-Path $KnownHostsFile).Path)",
    "-i", (Resolve-Path $IdentityFile).Path,
    "-R", "127.0.0.1:${RemotePort}:127.0.0.1:${LocalPort}",
    "${SshUser}@${HostName}"
)

do {
    & $ssh.Source @arguments
    $exitCode = $LASTEXITCODE
    if ($Once) { exit $exitCode }
    Start-Sleep -Seconds $ReconnectDelaySeconds
} while ($true)
