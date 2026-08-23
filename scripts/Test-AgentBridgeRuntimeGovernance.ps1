[CmdletBinding()]
param(
    [string]$VenvPath = "",
    [string]$LiveHome = "",
    [string]$BackupOutputDirectory = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

if (-not $VenvPath) {
    $cacheRoot = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { $env:USERPROFILE }
    $VenvPath = Join-Path $cacheRoot "AgentBridge\test-venv-py312"
}
$python = Join-Path ([IO.Path]::GetFullPath($VenvPath)) "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    $python = (Get-Command python -ErrorAction Stop).Source
}

Push-Location $repoRoot
try {
    & $python -m unittest tests.test_runtime_governance tests.test_runtime_backup
    if ($LASTEXITCODE -ne 0) {
        throw "Runtime governance fault-injection tests failed"
    }

    $backup = $null
    if ($LiveHome) {
        if (-not $BackupOutputDirectory) {
            $BackupOutputDirectory = Join-Path $repoRoot "output\runtime-backups"
        }
        $raw = & $python -m bscli.cli.main --home $LiveHome diagnostics backup-create --output-dir $BackupOutputDirectory
        if ($LASTEXITCODE -ne 0) {
            throw "Runtime-state backup creation failed"
        }
        $backup = ($raw | Out-String) | ConvertFrom-Json
        $validationRaw = & $python -m bscli.cli.main --home $LiveHome diagnostics backup-validate --backup $backup.manifestPath
        if ($LASTEXITCODE -ne 0) {
            throw "Runtime-state backup isolation validation failed"
        }
        $backup = ($validationRaw | Out-String) | ConvertFrom-Json
    }

    [ordered]@{
        status = "succeeded"
        syntheticFaults = @(
            "unknown_write",
            "stalled_operation",
            "stalled_task",
            "stalled_delivery",
            "identity_isolation",
            "backup_tampering"
        )
        realBusinessWrites = 0
        backup = $backup
    } | ConvertTo-Json -Depth 8
}
finally {
    Pop-Location
}
