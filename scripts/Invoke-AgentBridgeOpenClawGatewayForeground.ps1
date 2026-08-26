[CmdletBinding()]
param(
    [string]$GatewayLauncher = (
        "$env:LOCALAPPDATA\AgentBridge\openclaw-gateway-runtime.cmd"
    )
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $GatewayLauncher -PathType Leaf)) {
    throw "OpenClaw Gateway launcher was not found: $GatewayLauncher"
}

$Host.UI.RawUI.WindowTitle = "AgentBridge OpenClaw Gateway"
Write-Host "AgentBridge OpenClaw Gateway"
Write-Host "This window is the active Gateway host. Closing it stops the Gateway."
Write-Host "Started at: $([DateTimeOffset]::Now.ToString('yyyy-MM-dd HH:mm:ss zzz'))"
Write-Host ""

& (Resolve-Path -LiteralPath $GatewayLauncher).Path
$exitCode = $LASTEXITCODE
Write-Host ""
Write-Host "OpenClaw Gateway exited with code $exitCode. The self-heal guard will restart it."
exit $exitCode
