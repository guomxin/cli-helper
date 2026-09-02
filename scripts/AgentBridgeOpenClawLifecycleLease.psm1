Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Set-AgentBridgeOpenClawLifecycleLease {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][int]$GatewayPort,
        [Parameter(Mandatory = $true)][string]$OperationId,
        [Parameter(Mandatory = $true)][string]$Action,
        [Parameter(Mandatory = $true)]
        [ValidateSet("active", "completed", "failed")]
        [string]$State,
        [Parameter(Mandatory = $true)][string]$Phase,
        [Parameter(Mandatory = $true)][DateTimeOffset]$StartedAt,
        [int]$OwnerProcessId = $PID,
        [ValidateRange(5, 300)][int]$LeaseSeconds = 45,
        [string]$ErrorCode = ""
    )

    $now = [DateTimeOffset]::UtcNow
    $record = [ordered]@{
        schemaVersion = "agentbridge.openclaw-lifecycle-operation.v1"
        operationId = $OperationId
        gatewayPort = $GatewayPort
        action = $Action
        state = $State
        phase = $Phase
        ownerProcessId = $OwnerProcessId
        startedAt = $StartedAt.ToUniversalTime().ToString("o")
        heartbeatAt = $now.ToString("o")
        expiresAt = if ($State -eq "active") {
            $now.AddSeconds($LeaseSeconds).ToString("o")
        } else {
            $null
        }
        errorCode = if ($ErrorCode) { $ErrorCode } else { $null }
    }
    $parent = Split-Path -Parent $Path
    if ($parent) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $tempPath = "{0}.{1}.tmp" -f $Path, $OperationId
    [IO.File]::WriteAllText(
        $tempPath,
        ($record | ConvertTo-Json -Compress),
        [Text.UTF8Encoding]::new($false)
    )
    Move-Item -LiteralPath $tempPath -Destination $Path -Force
    return [pscustomobject]$record
}

function Get-AgentBridgeOpenClawLifecycleLease {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][int]$GatewayPort,
        [DateTimeOffset]$Now = [DateTimeOffset]::UtcNow
    )

    $result = [ordered]@{
        active = $false
        reason = "missing"
        operationId = $null
        action = $null
        state = $null
        phase = $null
        ownerProcessId = $null
        startedAt = $null
        heartbeatAt = $null
        heartbeatAgeSeconds = $null
        expiresAt = $null
    }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return [pscustomobject]$result
    }

    try {
        $record = Get-Content -LiteralPath $Path -Raw -Encoding utf8 |
            ConvertFrom-Json
        $result.operationId = [string]$record.operationId
        $result.action = [string]$record.action
        $result.state = [string]$record.state
        $result.phase = [string]$record.phase
        $result.ownerProcessId = [int]$record.ownerProcessId
        $result.startedAt = [string]$record.startedAt
        $result.heartbeatAt = [string]$record.heartbeatAt
        $result.expiresAt = [string]$record.expiresAt
        if ($record.schemaVersion -ne
            "agentbridge.openclaw-lifecycle-operation.v1") {
            $result.reason = "unsupported_schema"
            return [pscustomobject]$result
        }
        if ([int]$record.gatewayPort -ne $GatewayPort) {
            $result.reason = "other_gateway"
            return [pscustomobject]$result
        }
        if ($record.state -ne "active") {
            $result.reason = "inactive"
            return [pscustomobject]$result
        }
        $heartbeatAt = [DateTimeOffset]::Parse([string]$record.heartbeatAt)
        $expiresAt = [DateTimeOffset]::Parse([string]$record.expiresAt)
        $result.heartbeatAgeSeconds = [Math]::Round(
            ($Now - $heartbeatAt).TotalSeconds,
            1
        )
        if ($expiresAt -le $Now) {
            $result.reason = "expired"
            return [pscustomobject]$result
        }
        if (-not (Get-Process -Id ([int]$record.ownerProcessId) `
            -ErrorAction SilentlyContinue)) {
            $result.reason = "owner_missing"
            return [pscustomobject]$result
        }
        $result.active = $true
        $result.reason = "active"
        return [pscustomobject]$result
    }
    catch {
        $result.reason = "invalid"
        return [pscustomobject]$result
    }
}

Export-ModuleMember -Function @(
    "Set-AgentBridgeOpenClawLifecycleLease",
    "Get-AgentBridgeOpenClawLifecycleLease"
)
