[CmdletBinding()]
param(
    [string]$HostName = "10.10.50.213",
    [string]$SshUser = "root",
    [string]$IdentityFile = "$env:USERPROFILE\.ssh\id_ed25519_10_10_50_213",
    [string]$KnownHostsFile = "",
    [string]$RemoteRoot = "/home/guomao/agentbridge"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $KnownHostsFile) {
    $KnownHostsFile = Join-Path $repoRoot "deploy\ssh\agentbridge_known_hosts"
}
if ($HostName -notmatch '^[A-Za-z0-9.-]+$') { throw "HostName contains unsupported characters" }
if ($SshUser -notmatch '^[A-Za-z0-9._-]+$') { throw "SshUser contains unsupported characters" }
if ($RemoteRoot -notmatch '^/home/[A-Za-z0-9._/-]+$' -or $RemoteRoot.Contains("..")) {
    throw "RemoteRoot must be a fixed path below /home"
}
foreach ($path in @($IdentityFile, $KnownHostsFile)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required SSH file was not found: $path"
    }
}

$ssh = Get-Command ssh.exe -ErrorAction SilentlyContinue
if (-not $ssh) { $ssh = Get-Command ssh -ErrorAction Stop }
$connectionArguments = @(
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=15",
    "-o", "IdentitiesOnly=yes",
    "-o", "UserKnownHostsFile=$((Resolve-Path $KnownHostsFile).Path)",
    "-i", (Resolve-Path $IdentityFile).Path
)
$remoteCommand = @(
    "set -euo pipefail",
    "manifest=`$(find '$RemoteRoot/backups' -maxdepth 1 -type f -name 'agentbridge-*.manifest.json' -printf '%T@ %p\n' | sort -nr | head -n 1 | cut -d' ' -f2-)",
    'test -n "$manifest"',
    "install -d -m 0700 -o agentbridge -g agentbridge '$RemoteRoot/data/restore-drills'",
    "runuser -u agentbridge -- env HOME='$RemoteRoot' AGENTBRIDGE_RELEASE_ID=`$(sed -n 's/^AGENTBRIDGE_RELEASE_ID=//p' '$RemoteRoot/config/release.env' | head -n 1) '$RemoteRoot/venv/bin/python' -P -m bscli.cli.main --home '$RemoteRoot/data' diagnostics backup-restore-drill --manifest `"`$manifest`" --output-dir '$RemoteRoot/data/restore-drills'"
) -join "; "
$raw = (& $ssh.Source @connectionArguments "$SshUser@$HostName" $remoteCommand | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or -not $raw) {
    throw "AgentBridge isolated restore drill returned no result"
}
try {
    $report = $raw | ConvertFrom-Json
}
catch {
    throw "AgentBridge isolated restore drill returned invalid JSON: $raw"
}
if (-not $report.passed -or -not $report.sourceHashMatches) {
    throw "AgentBridge isolated restore drill did not pass"
}
if (-not $report.readOnlyOpen -or -not $report.writeRejected) {
    throw "Restored runtime database was not proven read-only"
}
if ($report.businessCalls -ne 0 -or $report.businessListReads -ne 0 -or $report.businessWrites -ne 0) {
    throw "Restore drill crossed the zero-business-side-effect boundary"
}

[ordered]@{
    status = "succeeded"
    checkedAt = [DateTimeOffset]::UtcNow.ToString("o")
    report = $report
} | ConvertTo-Json -Compress -Depth 10
