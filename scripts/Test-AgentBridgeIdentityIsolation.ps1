[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateCount(1, 32)]
    [string[]]$IdentityLabel,

    [ValidateSet("SessionStatus", "TaihuaSessionStatus")]
    [string]$Check = "SessionStatus",

    [ValidateRange(1, 1440)]
    [int]$Cycles = 1,

    [ValidateRange(0, 3600)]
    [int]$IntervalSeconds = 0,

    [string]$CaCertificate = "",
    [string]$OpenClawConfig = "",
    [string]$OpenClawEnvFile = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$smokeScript = Join-Path $PSScriptRoot "Test-AgentBridgeMcp.ps1"
$baselines = @{}
$latest = @{}

for ($cycle = 1; $cycle -le $Cycles; $cycle++) {
    foreach ($label in $IdentityLabel) {
        $arguments = @{
            Check = $Check
            IdentityLabel = $label
        }
        if ($CaCertificate) {
            $arguments.CaCertificate = $CaCertificate
        }
        if ($OpenClawConfig) {
            $arguments.OpenClawConfig = $OpenClawConfig
        }
        if ($OpenClawEnvFile) {
            $arguments.OpenClawEnvFile = $OpenClawEnvFile
        }

        $raw = (& $smokeScript @arguments | Out-String).Trim()
        if (-not $raw) {
            throw "AgentBridge identity smoke returned no result"
        }
        try {
            $result = $raw | ConvertFrom-Json
        }
        catch {
            throw "AgentBridge identity smoke returned invalid JSON"
        }
        if ($result.status -ne "succeeded") {
            throw "AgentBridge identity smoke failed for '$label'"
        }
        if ($result.sessionStatus -ne "active") {
            throw "AgentBridge session is not active for '$label'"
        }
        if (-not $result.userSubject -or -not $result.sessionId) {
            throw "AgentBridge identity smoke omitted its isolation identity"
        }

        $key = $label.ToLowerInvariant()
        $fingerprint = "$($result.systemId)|$($result.userSubject)|$($result.sessionId)"
        if ($baselines.ContainsKey($key) -and $baselines[$key] -ne $fingerprint) {
            throw "AgentBridge identity changed during the stability check for '$label'"
        }
        $baselines[$key] = $fingerprint
        $latest[$key] = [ordered]@{
            label = $label
            userSubject = $result.userSubject
            downstreamPrincipalRef = $result.downstreamPrincipalRef
            systemId = $result.systemId
            sessionStatus = $result.sessionStatus
            keepaliveState = $result.keepaliveState
            lastActivityAt = $result.lastActivityAt
            lastKeepaliveAt = $result.lastKeepaliveAt
            checkedAt = $result.checkedAt
        }
    }

    $subjects = @($latest.Values | ForEach-Object { $_.userSubject } | Sort-Object -Unique)
    if ($subjects.Count -ne $latest.Count) {
        throw "Multiple identity labels resolved to the same AgentBridge user subject"
    }
    if ($cycle -lt $Cycles -and $IntervalSeconds -gt 0) {
        Start-Sleep -Seconds $IntervalSeconds
    }
}

[ordered]@{
    status = "succeeded"
    check = $Check
    cycles = $Cycles
    intervalSeconds = $IntervalSeconds
    identityCount = $latest.Count
    uniqueSubjects = $true
    identities = @($latest.Values)
} | ConvertTo-Json -Compress -Depth 6
