param(
    [string]$Output,
    [switch]$SkipOpenClaw
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Arguments = @("$PSScriptRoot\run_agent_host_conformance.py")
if ($Output) {
    $Arguments += @('--output', $Output)
}
if ($SkipOpenClaw) {
    $Arguments += '--skip-openclaw'
}

Push-Location $Root
try {
    & python @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Agent Host compatibility suite failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
