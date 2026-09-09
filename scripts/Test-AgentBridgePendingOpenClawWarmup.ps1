[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Import-Module (Join-Path $PSScriptRoot "AgentBridgeOpenClawRestartPolicy.psm1") -Force
$pendingPath = Join-Path $env:LOCALAPPDATA "AgentBridge\openclaw-release-warmup.pending"
Complete-AgentBridgeOpenClawWarmup -PendingPath $pendingPath -GetPlan {
    Get-AgentBridgeOpenClawRestartPlan -RepoRoot $repoRoot
} -Warmup {
    & (Join-Path $PSScriptRoot "Test-OpenClawGatewayWarmup.ps1") | Out-String | ConvertFrom-Json
} | ConvertTo-Json -Compress
