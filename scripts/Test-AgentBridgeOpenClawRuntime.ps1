[CmdletBinding()]
param(
    [ValidateRange(1, 65535)][int]$GatewayPort = 18789,
    # Retained for CLI compatibility. Freshness is now tied to the current
    # Gateway process start, not to an arbitrary wall-clock age.
    [ValidateRange(30, 900)][int]$RegistrationMaxAgeSeconds = 300,
    [string]$PluginManifestPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ready = Invoke-RestMethod `
    -Uri "http://127.0.0.1:$GatewayPort/readyz" `
    -TimeoutSec 5 `
    -Method Get
if (-not [bool]$ready.ready -or @($ready.failing).Count -ne 0) {
    throw "OpenClaw Gateway ready endpoint is not healthy"
}

$listeners = @(
    Get-NetTCPConnection -State Listen -LocalPort $GatewayPort `
        -ErrorAction SilentlyContinue |
        Sort-Object OwningProcess -Unique
)
$gateways = @(
    Get-CimInstance Win32_Process -Filter "Name = 'node.exe'" `
        -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -match 'openclaw\.mjs.*gateway run' -and
            $_.CommandLine -match "--port\s+$GatewayPort(?:\s|$)"
        }
)
if ($listeners.Count -ne 1 -or $gateways.Count -ne 1 -or
    [int]$listeners[0].OwningProcess -ne [int]$gateways[0].ProcessId) {
    throw "OpenClaw Gateway does not have exactly one matching process and listener"
}

if (-not $PluginManifestPath) {
    $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
    $PluginManifestPath = Join-Path `
        $repoRoot `
        "integrations\openclaw-agentbridge\package.json"
}
if (-not (Test-Path -LiteralPath $PluginManifestPath -PathType Leaf)) {
    throw "AgentBridge OpenClaw plugin manifest was not found"
}
$manifest = Get-Content -LiteralPath $PluginManifestPath -Raw | ConvertFrom-Json
$expectedVersion = [string]$manifest.version
if (-not $expectedVersion) {
    throw "AgentBridge OpenClaw plugin manifest has no version"
}

$logRoot = Join-Path ([IO.Path]::GetTempPath()) "openclaw"
$logFiles = @(
    Get-ChildItem -LiteralPath $logRoot -Filter "openclaw-*.log" -File `
        -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending
)
function Find-LatestPluginRegistration {
    $latest = $null
    foreach ($logFile in $logFiles) {
        foreach ($line in @(Get-Content -LiteralPath $logFile.FullName -Tail 4000)) {
            try { $entry = $line | ConvertFrom-Json } catch { continue }
            $message = if ($entry.PSObject.Properties.Name -contains "message") {
                [string]$entry.message
            } else {
                ""
            }
            if ($message -notmatch '^AgentBridge interaction plugin registered \(version=([^,]+),') {
                continue
            }
            $observedAt = [DateTimeOffset]::Parse([string]$entry.time)
            if (-not $latest -or $observedAt -gt $latest.observedAt) {
                $latest = [pscustomobject]@{
                    observedAt = $observedAt
                    version = [string]$Matches[1]
                    logPath = $logFile.FullName
                }
            }
        }
        if ($latest) { break }
    }
    return $latest
}

$registration = Find-LatestPluginRegistration
if (-not $registration) {
    throw "AgentBridge OpenClaw plugin registration was not found in current logs"
}
$gatewayStartedAt = [DateTimeOffset]([DateTime]$gateways[0].CreationDate)
if ($registration.observedAt -lt $gatewayStartedAt.AddSeconds(-5)) {
    throw "AgentBridge OpenClaw plugin was not registered by the current Gateway process"
}
if ($registration.version -ne $expectedVersion) {
    throw "AgentBridge OpenClaw plugin runtime version does not match its manifest"
}

[ordered]@{
    status = "succeeded"
    checkedAt = [DateTimeOffset]::UtcNow.ToString("o")
    gatewayProcessId = [int]$gateways[0].ProcessId
    gatewayStartedAt = $gatewayStartedAt.ToString("o")
    listenerAddress = [string]$listeners[0].LocalAddress
    readyEndpoint = "ok"
    pluginStatus = "loaded"
    pluginVersion = $registration.version
    pluginRegistrationAt = $registration.observedAt.ToString("o")
    pluginRegistrationAgeSeconds = [Math]::Round(
        ([DateTimeOffset]::Now - $registration.observedAt).TotalSeconds,
        3
    )
    pluginRegistrationFreshnessPolicy = "current_gateway_process"
    businessCalls = 0
    businessListReads = 0
    businessWrites = 0
} | ConvertTo-Json -Compress -Depth 4
