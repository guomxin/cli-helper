[CmdletBinding()]
param(
    [string]$HostName = "10.10.50.213",
    [string]$SshUser = "root",
    [string]$IdentityFile = "",
    [string]$KnownHostsFile = "",
    [string]$RemoteRoot = "/home/guomao/agentbridge",
    [string]$ServiceName = "agentbridge",
    [string]$AgentBridgeBaseUrl = "https://10.10.50.213",
    [string]$CaCertificate = "",
    [string[]]$IdentityLabel = @(),
    [ValidateRange(5, 300)][int]$OpenClawTimeoutSeconds = 90,
    [switch]$SkipOpenClaw
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($HostName -notmatch '^[A-Za-z0-9.-]+$') {
    throw "HostName contains unsupported characters"
}
if ($SshUser -notmatch '^[A-Za-z0-9._-]+$') {
    throw "SshUser contains unsupported characters"
}
if ($ServiceName -notmatch '^[A-Za-z0-9_.@-]+$') {
    throw "ServiceName contains unsupported characters"
}
if ($RemoteRoot -notmatch '^/home/[A-Za-z0-9._/-]+$' -or $RemoteRoot.Contains("..")) {
    throw "RemoteRoot must be a fixed path below /home"
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

function Invoke-HealthCheck {
    param([Parameter(Mandatory = $true)][string]$Uri)

    $response = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 15
    if ($response.StatusCode -ne 200) {
        throw "Health check failed: $Uri"
    }
    $payload = $response.Content | ConvertFrom-Json
    if ($payload.status -ne "ok") {
        throw "Health check returned an unexpected status: $Uri"
    }
    return $payload
}

function Invoke-OpenClawJson {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Label
    )

    $command = Get-Command openclaw.cmd -ErrorAction SilentlyContinue
    if (-not $command) {
        $command = Get-Command openclaw -ErrorAction Stop
    }
    $nonce = [Guid]::NewGuid().ToString("N")
    $stdoutPath = Join-Path ([IO.Path]::GetTempPath()) "agentbridge-openclaw-$nonce.out"
    $stderrPath = Join-Path ([IO.Path]::GetTempPath()) "agentbridge-openclaw-$nonce.err"
    try {
        $process = Start-Process `
            -FilePath $command.Source `
            -ArgumentList $Arguments `
            -WindowStyle Hidden `
            -RedirectStandardOutput $stdoutPath `
            -RedirectStandardError $stderrPath `
            -PassThru
        if (-not $process.WaitForExit($OpenClawTimeoutSeconds * 1000)) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            $process.WaitForExit()
            throw "$Label timed out after $OpenClawTimeoutSeconds seconds"
        }
        $stdout = [IO.File]::ReadAllText($stdoutPath).Trim()
        $stderr = [IO.File]::ReadAllText($stderrPath).Trim()
        if ($process.ExitCode -ne 0) {
            $detail = if ($stderr) { $stderr } elseif ($stdout) { $stdout } else { "no output" }
            throw "$Label failed with exit code $($process.ExitCode): $detail"
        }
        if (-not $stdout) {
            throw "$Label returned no JSON"
        }
        try {
            return $stdout | ConvertFrom-Json
        }
        catch {
            throw "$Label returned invalid JSON: $($_.Exception.Message)"
        }
    }
    finally {
        Remove-Item -LiteralPath $stdoutPath, $stderrPath -Force -ErrorAction SilentlyContinue
    }
}

$mcpArguments = @{ Check = "Release" }
if ($CaCertificate) {
    $mcpArguments.CaCertificate = $CaCertificate
}
$releaseRaw = (
    & (Join-Path $PSScriptRoot "Test-AgentBridgeMcp.ps1") @mcpArguments |
        Out-String
).Trim()
if (-not $releaseRaw) {
    throw "AgentBridge release smoke returned no result"
}
$release = $releaseRaw | ConvertFrom-Json

$adminHealth = Invoke-HealthCheck -Uri "$AgentBridgeBaseUrl`:8782/healthz"
$workspaceHealth = Invoke-HealthCheck -Uri "$AgentBridgeBaseUrl`:8783/healthz"

$ssh = Get-Command ssh.exe -ErrorAction SilentlyContinue
if (-not $ssh) { $ssh = Get-Command ssh -ErrorAction Stop }
$connectionArguments = @(
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=15",
    "-o", "UserKnownHostsFile=$((Resolve-Path $KnownHostsFile).Path)",
    "-i", (Resolve-Path $IdentityFile).Path
)
$remoteCommand = @(
    "set -euo pipefail",
    "state=`$(systemctl is-active '$ServiceName' || true)",
    "main_pid=`$(systemctl show '$ServiceName' -p MainPID --value)",
    "release_id=`$(sed -n 's/^AGENTBRIDGE_RELEASE_ID=//p' '$RemoteRoot/config/release.env' | head -n 1)",
    "error_count=`$(journalctl -q -u '$ServiceName' --since '-30 minutes' --priority=err --no-pager | wc -l)",
    'echo "serviceState=$state"',
    'echo "mainPid=$main_pid"',
    'echo "releaseId=$release_id"',
    'echo "recentErrorCount=$error_count"'
) -join "; "
$remoteRaw = (
    & $ssh.Source @connectionArguments "$SshUser@$HostName" $remoteCommand |
        Out-String
).Trim()
if ($LASTEXITCODE -ne 0 -or -not $remoteRaw) {
    throw "AgentBridge remote runtime check failed"
}
$remoteValues = @{}
foreach ($line in $remoteRaw -split '\r?\n') {
    if ($line -match '^([A-Za-z][A-Za-z0-9]*)=(.*)$') {
        $remoteValues[$Matches[1]] = $Matches[2]
    }
}
$requiredRemoteKeys = @(
    "serviceState",
    "mainPid",
    "releaseId",
    "recentErrorCount"
)
foreach ($key in $requiredRemoteKeys) {
    if (-not $remoteValues.ContainsKey($key)) {
        $receivedKeys = @($remoteValues.Keys) -join ","
        throw "AgentBridge remote runtime output is missing $key (received: $receivedKeys)"
    }
}
$remote = [ordered]@{
    serviceState = $remoteValues["serviceState"]
    mainPid = [int64]$remoteValues["mainPid"]
    releaseId = $remoteValues["releaseId"]
    recentErrorCount = [int]$remoteValues["recentErrorCount"]
}
if ($remote.serviceState -ne "active" -or [int64]$remote.mainPid -le 0) {
    throw "AgentBridge service is not active"
}

$openClaw = $null
if (-not $SkipOpenClaw) {
    $gateway = Invoke-OpenClawJson `
        -Arguments @("gateway", "status", "--deep", "--require-rpc", "--json") `
        -Label "OpenClaw Gateway deep RPC check"
    $plugin = Invoke-OpenClawJson `
        -Arguments @("plugins", "inspect", "agentbridge-interactions", "--json") `
        -Label "OpenClaw AgentBridge plugin check"
    if (-not $gateway.rpc.ok) {
        throw "OpenClaw Gateway deep RPC check failed"
    }
    if ($plugin.plugin.status -ne "loaded") {
        throw "AgentBridge OpenClaw plugin is not loaded"
    }
    $openClaw = [ordered]@{
        rpc = "ok"
        cliVersion = $gateway.cli.version
        gatewayVersion = $gateway.gateway.version
        pluginStatus = $plugin.plugin.status
        pluginVersion = $plugin.plugin.version
        versionDriftCount = @($gateway.pluginVersionDrift.drifts).Count
    }
    if ($openClaw.cliVersion -ne $openClaw.gatewayVersion) {
        throw "OpenClaw CLI and Gateway versions do not match"
    }
    if ($openClaw.versionDriftCount -ne 0) {
        throw "OpenClaw reports plugin version drift"
    }
}

$identityIsolation = $null
if ($IdentityLabel.Count -gt 0) {
    if ($IdentityLabel.Count -lt 2) {
        throw "IdentityLabel requires at least two identities"
    }
    $isolationArguments = @{
        IdentityLabel = $IdentityLabel
        IdentityFile = (Resolve-Path $IdentityFile).Path
        KnownHostsFile = (Resolve-Path $KnownHostsFile).Path
    }
    if ($CaCertificate) {
        $isolationArguments.CaCertificate = $CaCertificate
    }
    $identityIsolation = (
        & (Join-Path $PSScriptRoot "Test-AgentBridgeOmnichannelIsolation.ps1") `
            @isolationArguments |
            Out-String
    ).Trim() | ConvertFrom-Json
}

[ordered]@{
    status = "succeeded"
    checkedAt = [DateTimeOffset]::UtcNow.ToString("o")
    businessWrites = 0
    businessListReads = 0
    release = $release
    adminHealth = $adminHealth
    workspaceHealth = $workspaceHealth
    remoteRuntime = $remote
    openClaw = $openClaw
    identityIsolation = $identityIsolation
} | ConvertTo-Json -Compress -Depth 12
