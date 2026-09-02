[CmdletBinding()]
param(
    [ValidateRange(1, 65535)][int]$GatewayPort = 18789,
    # Retained for CLI compatibility. Freshness is now tied to the current
    # Gateway process start, not to an arbitrary wall-clock age.
    [ValidateRange(30, 900)][int]$RegistrationMaxAgeSeconds = 300,
    [ValidateRange(10, 120)][int]$StabilizationTimeoutSeconds = 45,
    [string]$PluginManifestPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ready = $null
$readinessAttempts = 0
$readinessError = "unavailable"
$readinessDeadline = [DateTimeOffset]::UtcNow.AddSeconds(
    $StabilizationTimeoutSeconds
)
do {
    $readinessAttempts++
    try {
        $candidate = Invoke-RestMethod `
            -Uri "http://127.0.0.1:$GatewayPort/readyz" `
            -TimeoutSec 5 `
            -Method Get
        if ([bool]$candidate.ready -and @($candidate.failing).Count -eq 0) {
            $ready = $candidate
            break
        }
        $readinessError = "not_healthy"
    }
    catch {
        $readinessError = $_.Exception.GetType().Name
    }
    if ([DateTimeOffset]::UtcNow -lt $readinessDeadline) {
        Start-Sleep -Seconds 1
    }
} while ([DateTimeOffset]::UtcNow -lt $readinessDeadline)
if (-not $ready) {
    throw (
        "OpenClaw Gateway ready endpoint did not stabilize within {0}s; last={1}" -f `
            $StabilizationTimeoutSeconds, $readinessError
    )
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
function Find-LatestPluginRegistration {
    $latest = $null
    foreach ($logFile in @(
        Get-ChildItem -LiteralPath $logRoot -Filter "openclaw-*.log" -File `
            -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending
    )) {
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

$gatewayStartedAt = [DateTimeOffset]([DateTime]$gateways[0].CreationDate)
$registration = $null
$registrationDeadline = [DateTimeOffset]::UtcNow.AddSeconds(
    $StabilizationTimeoutSeconds
)
do {
    $candidateRegistration = Find-LatestPluginRegistration
    if ($candidateRegistration -and
        $candidateRegistration.observedAt -ge $gatewayStartedAt.AddSeconds(-5)) {
        $registration = $candidateRegistration
        break
    }
    if ([DateTimeOffset]::UtcNow -lt $registrationDeadline) {
        Start-Sleep -Seconds 1
    }
} while ([DateTimeOffset]::UtcNow -lt $registrationDeadline)
if (-not $registration) {
    throw (
        "AgentBridge OpenClaw plugin was not registered by the current " +
        "Gateway process within the stabilization window"
    )
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
    readinessAttempts = $readinessAttempts
    stabilizationTimeoutSeconds = $StabilizationTimeoutSeconds
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
