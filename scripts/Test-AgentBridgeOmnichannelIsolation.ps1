[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateCount(2, 32)]
    [string[]]$IdentityLabel,
    [string[]]$ExpectedEndpoint = @(
        "guomao=web",
        "guomao=telegram",
        "lishiyu=web",
        "lishiyu=openclaw-weixin"
    ),
    [string]$HostName = "10.10.50.213",
    [string]$SshUser = "root",
    [string]$IdentityFile = "",
    [string]$KnownHostsFile = "",
    [string]$RemoteRoot = "/home/guomao/agentbridge",
    [string]$CaCertificate = "",
    [string]$OpenClawConfig = "",
    [string]$OpenClawEnvFile = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($HostName -notmatch '^[A-Za-z0-9.-]+$') {
    throw "HostName contains unsupported characters"
}
if ($SshUser -notmatch '^[A-Za-z0-9._-]+$') {
    throw "SshUser contains unsupported characters"
}
if ($RemoteRoot -notmatch '^/home/[A-Za-z0-9._/-]+$' -or $RemoteRoot.Contains("..")) {
    throw "RemoteRoot must be a fixed path below /home"
}
foreach ($expectation in $ExpectedEndpoint) {
    if ($expectation -notmatch '^[A-Za-z0-9_.@-]+=[A-Za-z0-9_.-]+$') {
        throw "ExpectedEndpoint must use USER_SUBJECT=CLIENT_TYPE"
    }
}
if (-not $IdentityFile) {
    $IdentityFile = Join-Path $env:USERPROFILE ".ssh\id_ed25519_10_10_50_213"
}
if (-not $KnownHostsFile) {
    $KnownHostsFile = Join-Path (
        (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
    ) "deploy\ssh\agentbridge_known_hosts"
}
if (-not (Test-Path -LiteralPath $IdentityFile -PathType Leaf)) {
    throw "SSH identity file was not found"
}
if (-not (Test-Path -LiteralPath $KnownHostsFile -PathType Leaf)) {
    throw "SSH known-hosts file was not found"
}

$identityScript = Join-Path $PSScriptRoot "Test-AgentBridgeIdentityIsolation.ps1"
$identityArguments = @{
    IdentityLabel = $IdentityLabel
    Check = "SessionStatus"
    Cycles = 2
    IntervalSeconds = 1
}
if ($CaCertificate) {
    $identityArguments.CaCertificate = $CaCertificate
}
if ($OpenClawConfig) {
    $identityArguments.OpenClawConfig = $OpenClawConfig
}
if ($OpenClawEnvFile) {
    $identityArguments.OpenClawEnvFile = $OpenClawEnvFile
}
$identityRaw = (& $identityScript @identityArguments | Out-String).Trim()
if (-not $identityRaw) {
    throw "AgentBridge identity isolation check returned no result"
}
$identityResult = $identityRaw | ConvertFrom-Json
if ($identityResult.status -ne "succeeded") {
    throw "AgentBridge identity isolation check failed"
}

$ssh = Get-Command ssh.exe -ErrorAction SilentlyContinue
if (-not $ssh) { $ssh = Get-Command ssh -ErrorAction Stop }
$connectionArguments = @(
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=15",
    "-o", "UserKnownHostsFile=$((Resolve-Path $KnownHostsFile).Path)",
    "-i", (Resolve-Path $IdentityFile).Path
)
$remoteArguments = @(
    "set -euo pipefail;",
    "'$RemoteRoot/venv/bin/python'", "-P", "-m", "bscli.cli.main",
    "--home", "'$RemoteRoot/data'", "diagnostics", "omnichannel"
)
foreach ($expectation in $ExpectedEndpoint) {
    $remoteArguments += @("--expect-endpoint", "'$expectation'")
}
$remoteCommand = $remoteArguments -join " "
$target = "$SshUser@$HostName"
$runtimeRaw = (& $ssh.Source @connectionArguments $target $remoteCommand | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or -not $runtimeRaw) {
    throw "AgentBridge remote omnichannel diagnostics failed: $runtimeRaw"
}
$runtimeResult = $runtimeRaw | ConvertFrom-Json
if ($runtimeResult.status -ne "succeeded") {
    throw "AgentBridge remote omnichannel isolation did not pass"
}

[ordered]@{
    status = "succeeded"
    checkedAt = [DateTimeOffset]::UtcNow.ToString("o")
    businessWrites = 0
    pendingReads = 0
    identityIsolation = $identityResult
    endpointIsolation = $runtimeResult
} | ConvertTo-Json -Compress -Depth 12
