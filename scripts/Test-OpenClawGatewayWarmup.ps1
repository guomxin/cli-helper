[CmdletBinding()]
param(
    [string]$AgentId = "main",
    [int]$ColdTimeoutSeconds = 420,
    [int]$HotTimeoutSeconds = 90,
    [int]$HotPathMaximumSeconds = 60
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($AgentId -notmatch '^[A-Za-z0-9_-]+$') {
    throw "AgentId contains unsupported characters"
}

$sessionKey = "agent:${AgentId}:agentbridge-release-warmup"

function Invoke-WarmupTurn {
    param(
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds
    )

    $message = "AgentBridge deployment warm-up ${Label}. Do not call any tool. Reply exactly READY."
    $stopwatch = [Diagnostics.Stopwatch]::StartNew()
    $raw = (& openclaw agent `
        --agent $AgentId `
        --session-key $sessionKey `
        --message $message `
        --thinking off `
        --timeout $TimeoutSeconds `
        --json) | Out-String
    $exitCode = $LASTEXITCODE
    $stopwatch.Stop()
    if ($exitCode -ne 0) {
        throw "OpenClaw Gateway warm-up failed during ${Label} (exit=${exitCode})"
    }

    try {
        $result = $raw | ConvertFrom-Json
    } catch {
        throw "OpenClaw Gateway warm-up returned invalid JSON during ${Label}"
    }
    $status = [string]$result.status
    $summary = [string]$result.summary
    $reply = ""
    if (
        $result.result -and
        $result.result.meta -and
        $result.result.meta.finalAssistantVisibleText
    ) {
        $reply = [string]$result.result.meta.finalAssistantVisibleText
    }
    if ($status -ne "ok" -or $summary -ne "completed" -or $reply -ne "READY") {
        throw "OpenClaw Gateway warm-up did not complete cleanly during ${Label}"
    }

    [pscustomobject]@{
        label = $Label
        durationSeconds = [Math]::Round($stopwatch.Elapsed.TotalSeconds, 3)
        transport = "gateway"
        reply = $reply
    }
}

$cold = Invoke-WarmupTurn -Label "cold" -TimeoutSeconds $ColdTimeoutSeconds
$hot = Invoke-WarmupTurn -Label "hot" -TimeoutSeconds $HotTimeoutSeconds
if ($hot.durationSeconds -gt $HotPathMaximumSeconds) {
    throw "OpenClaw hot-path warm-up remained too slow ($($hot.durationSeconds)s > ${HotPathMaximumSeconds}s)"
}

[ordered]@{
    status = "succeeded"
    sessionKey = $sessionKey
    cold = $cold
    hot = $hot
} | ConvertTo-Json -Depth 4 -Compress
