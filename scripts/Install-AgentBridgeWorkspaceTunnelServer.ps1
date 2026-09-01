[CmdletBinding()]
param(
    [string]$HostName = "10.10.50.213",
    [string]$SshUser = "root",
    [string]$IdentityFile = "$env:USERPROFILE\.ssh\id_ed25519_10_10_50_213",
    [string]$KnownHostsFile = "",
    [string]$RemoteConfigPath = "/etc/ssh/sshd_config.d/90-agentbridge-workspace-tunnel.conf"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $KnownHostsFile) {
    $KnownHostsFile = Join-Path $repoRoot "deploy\ssh\agentbridge_known_hosts"
}
$configPath = Join-Path `
    $repoRoot `
    "deploy\ssh\agentbridge_workspace_tunnel_sshd.conf"
if ($HostName -notmatch '^[A-Za-z0-9.-]+$') { throw "HostName contains unsupported characters" }
if ($SshUser -notmatch '^[A-Za-z0-9._-]+$') { throw "SshUser contains unsupported characters" }
if ($RemoteConfigPath -notmatch '^/etc/ssh/sshd_config\.d/[A-Za-z0-9._-]+\.conf$') {
    throw "RemoteConfigPath must be a fixed sshd_config.d file"
}
foreach ($path in @($IdentityFile, $KnownHostsFile, $configPath)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required SSH keepalive file was not found: $path"
    }
}

$ssh = (Get-Command ssh.exe -ErrorAction Stop).Source
$scp = (Get-Command scp.exe -ErrorAction Stop).Source
$knownHosts = (Resolve-Path -LiteralPath $KnownHostsFile).Path
$identity = (Resolve-Path -LiteralPath $IdentityFile).Path
$target = "${SshUser}@${HostName}"
$remoteTemp = "/tmp/agentbridge-workspace-tunnel-sshd-$PID.conf"
$common = @(
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=10",
    "-o", "IdentitiesOnly=yes",
    "-o", "UserKnownHostsFile=$knownHosts",
    "-i", $identity
)

& $scp @common -- $configPath "${target}:$remoteTemp"
if ($LASTEXITCODE -ne 0) { throw "Unable to upload the SSH keepalive configuration" }

$remoteScript = @'
set -eu
source_path="$1"
target_path="$2"
backup_path="${target_path}.agentbridge-backup"
had_original=0
if [ -f "$target_path" ]; then
    cp -p "$target_path" "$backup_path"
    had_original=1
fi
install -o root -g root -m 0644 "$source_path" "$target_path"
if ! "$(command -v sshd)" -t; then
    if [ "$had_original" -eq 1 ]; then
        mv -f "$backup_path" "$target_path"
    else
        rm -f "$target_path"
    fi
    rm -f "$source_path"
    exit 1
fi
rm -f "$backup_path" "$source_path"
if systemctl list-unit-files ssh.service >/dev/null 2>&1; then
    systemctl reload ssh.service
else
    systemctl reload sshd.service
fi
'@
$encodedScript = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($remoteScript))
$remoteCommand = (
    "printf '%s' '{0}' | base64 -d | sh -s -- '{1}' '{2}'" -f `
        $encodedScript, $remoteTemp, $RemoteConfigPath
)
& $ssh @common -- $target $remoteCommand
if ($LASTEXITCODE -ne 0) { throw "Unable to install or validate the SSH keepalive configuration" }

$verifyCommand = (
    "set -eu; test -f '{0}'; " +
    "grep -q '^Match User root$' '{0}'; " +
    "grep -q '^    ClientAliveInterval 5$' '{0}'; " +
    "grep -q '^    ClientAliveCountMax 2$' '{0}'; " +
    "grep -q '^Match all$' '{0}'; " +
    "sshd -t"
) -f $RemoteConfigPath
& $ssh @common -- $target $verifyCommand
if ($LASTEXITCODE -ne 0) { throw "SSH keepalive configuration verification failed" }

[ordered]@{
    status = "installed"
    target = $target
    remoteConfigPath = $RemoteConfigPath
    clientAliveIntervalSeconds = 5
    clientAliveCountMax = 2
    businessCalls = 0
    businessListReads = 0
    businessWrites = 0
} | ConvertTo-Json -Compress
