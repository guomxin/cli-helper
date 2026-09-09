Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-AgentBridgeOpenClawInputs {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [string]$GatewayLauncher = "$env:LOCALAPPDATA\AgentBridge\openclaw-gateway-runtime.cmd",
        [string]$ConfigPath = $(if ($env:OPENCLAW_CONFIG_PATH) {
            $env:OPENCLAW_CONFIG_PATH
        } else { "$env:USERPROFILE\.openclaw\openclaw.json" })
    )
    $root = [IO.Path]::GetFullPath($RepoRoot)
    $plugin = Join-Path $root "integrations\openclaw-agentbridge"
    $paths = @{}
    foreach ($name in @("index.js", "package.json", "openclaw.plugin.json")) {
        $path = Join-Path $plugin $name
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Required plugin input is missing: $name"
        }
        $paths["plugin/$name"] = $path
    }
    foreach ($name in @("package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "yarn.lock")) {
        $paths["plugin/$name"] = Join-Path $plugin $name
    }
    # lib contains executable modules and the generated business tool catalogue.
    # Documentation and test files outside lib never invalidate the runtime.
    foreach ($file in @(Get-ChildItem -LiteralPath (Join-Path $plugin "lib") -File -Recurse)) {
        $relative = $file.FullName.Substring($plugin.Length + 1).Replace("\", "/")
        $paths["plugin/$relative"] = $file.FullName
    }
    $paths["host/config"] = [IO.Path]::GetFullPath($ConfigPath)
    $paths["host/env"] = Join-Path (Split-Path -Parent $ConfigPath) ".env"
    $paths["host/launcher"] = [IO.Path]::GetFullPath($GatewayLauncher)
    $paths["workspace/env"] = Join-Path $root ".env"
    $files = @(
        foreach ($name in @($paths.Keys | Sort-Object)) {
            $path = $paths[$name]
            [ordered]@{
                name = $name
                path = $path
                hash = if (Test-Path -LiteralPath $path -PathType Leaf) {
                    $algorithm = [Security.Cryptography.SHA256]::Create()
                    $stream = [IO.File]::OpenRead($path)
                    try {
                        [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace("-", "").ToLowerInvariant()
                    } finally { $stream.Dispose(); $algorithm.Dispose() }
                } else { $null }
            }
        }
    )
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [Text.Encoding]::UTF8.GetBytes(($files | ConvertTo-Json -Compress -Depth 4))
        $fingerprint = [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace("-", "").ToLowerInvariant()
    } finally { $sha.Dispose() }
    [pscustomobject]@{ fingerprint = $fingerprint; files = $files }
}

function Get-AgentBridgeOpenClawRestartDecision {
    param(
        [Parameter(Mandatory = $true)]$Inputs,
        $Baseline = $null,
        [int]$GatewayProcessId = 0,
        [string]$GatewayStartedAt = "",
        [bool]$Ready = $false,
        [switch]$Force
    )
    $reason = "inputs_unchanged"
    $changed = @()
    if ($Force) { $reason = "forced" }
    elseif (-not $Ready -or $GatewayProcessId -le 0) { $reason = "gateway_not_ready" }
    elseif (-not $Baseline) { $reason = "baseline_missing" }
    else {
        try {
            if ($Baseline.schemaVersion -ne "agentbridge.openclaw-inputs.v1") {
                $reason = "baseline_invalid"
            } elseif ($Baseline.gatewayProcessId -ne $GatewayProcessId -or
                [DateTimeOffset]$Baseline.gatewayStartedAt -ne
                [DateTimeOffset]$GatewayStartedAt) {
                $reason = "gateway_process_changed"
            } elseif ($Baseline.inputs.fingerprint -ne $Inputs.fingerprint) {
                $reason = "inputs_changed"
                $old = @{}
                foreach ($file in $Baseline.inputs.files) { $old[$file.name] = $file.hash }
                foreach ($file in $Inputs.files) {
                    if (-not $old.ContainsKey($file.name) -or $old[$file.name] -ne $file.hash) {
                        $changed += $file.name
                    }
                    $old.Remove($file.name)
                }
                $changed += @($old.Keys)
            }
        } catch { $reason = "baseline_invalid" }
    }
    [pscustomobject]@{
        required = $reason -ne "inputs_unchanged"
        reason = $reason
        changedInputs = @($changed | Sort-Object)
        fingerprint = $Inputs.fingerprint
        gatewayProcessId = $GatewayProcessId
    }
}

function Get-AgentBridgeOpenClawRestartPlan {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [string]$GatewayLauncher = "$env:LOCALAPPDATA\AgentBridge\openclaw-gateway-runtime.cmd",
        [string]$BaselinePath = "$env:LOCALAPPDATA\AgentBridge\openclaw-runtime-inputs.json",
        [int]$GatewayPort = 18789,
        [switch]$Force
    )
    $inputs = Get-AgentBridgeOpenClawInputs -RepoRoot $RepoRoot -GatewayLauncher $GatewayLauncher
    $baseline = $null
    if (Test-Path -LiteralPath $BaselinePath -PathType Leaf) {
        try { $baseline = Get-Content -LiteralPath $BaselinePath -Raw | ConvertFrom-Json }
        catch { $baseline = $null }
    }
    $processes = @(
        Get-CimInstance Win32_Process -Filter "Name = 'node.exe'" |
        Where-Object {
            $_.CommandLine -match 'openclaw\.mjs.*gateway run' -and
            $_.CommandLine -match "--port\s+$GatewayPort(?:\s|$)"
        }
    )
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $GatewayPort -ErrorAction SilentlyContinue |
        Sort-Object OwningProcess -Unique)
    $ready = $false
    $processId = 0
    $startedAt = ""
    if ($processes.Count -eq 1 -and $listeners.Count -eq 1 -and
        $processes[0].ProcessId -eq $listeners[0].OwningProcess) {
        $processId = [int]$processes[0].ProcessId
        $startedAt = ([DateTimeOffset]$processes[0].CreationDate).ToString("o")
        try {
            $status = Invoke-RestMethod "http://127.0.0.1:$GatewayPort/readyz" -TimeoutSec 3
            $ready = [bool]$status.ready -and @($status.failing).Count -eq 0
        } catch { $ready = $false }
    }
    Get-AgentBridgeOpenClawRestartDecision -Inputs $inputs -Baseline $baseline `
        -GatewayProcessId $processId -GatewayStartedAt $startedAt -Ready $ready -Force:$Force
}

function Save-AgentBridgeOpenClawBaseline {
    param(
        [Parameter(Mandatory = $true)]$Before,
        [Parameter(Mandatory = $true)]$After,
        [Parameter(Mandatory = $true)][int]$GatewayProcessId,
        [Parameter(Mandatory = $true)][DateTimeOffset]$GatewayStartedAt,
        [string]$Path = "$env:LOCALAPPDATA\AgentBridge\openclaw-runtime-inputs.json"
    )
    if ($Before.fingerprint -ne $After.fingerprint) {
        throw "OpenClaw inputs changed during startup; refusing to certify the loaded inputs"
    }
    $record = [ordered]@{
        schemaVersion = "agentbridge.openclaw-inputs.v1"
        observedAt = [DateTimeOffset]::UtcNow.ToString("o")
        gatewayProcessId = $GatewayProcessId
        gatewayStartedAt = $GatewayStartedAt.ToString("o")
        inputs = $After
    }
    $temp = "$Path.$([guid]::NewGuid().ToString('N')).tmp"
    try {
        [IO.File]::WriteAllText($temp, ($record | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
        Move-Item -LiteralPath $temp -Destination $Path -Force
    } finally {
        if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Force }
    }
}

function Complete-AgentBridgeOpenClawWarmup {
    param(
        [Parameter(Mandatory = $true)][string]$PendingPath,
        [Parameter(Mandatory = $true)][scriptblock]$GetPlan,
        [Parameter(Mandatory = $true)][scriptblock]$Warmup
    )
    if (-not (Test-Path -LiteralPath $PendingPath)) {
        return [pscustomobject]@{ status = "skipped"; reason = "no_pending_warmup" }
    }
    $before = & $GetPlan
    if ($before.required) { throw "Gateway inputs are not verified before warm-up" }
    $result = & $Warmup
    if ($result.status -ne "succeeded") {
        throw "OpenClaw cold/hot warm-up failed; acceptance remains pending"
    }
    $after = & $GetPlan
    if ($after.required -or $before.gatewayProcessId -ne $after.gatewayProcessId -or
        $before.fingerprint -ne $after.fingerprint) {
        throw "Gateway changed during warm-up; acceptance remains pending"
    }
    Remove-Item -LiteralPath $PendingPath -Force
    [pscustomobject]@{ status = "succeeded"; reason = "pending_warmup_completed" }
}

Export-ModuleMember -Function Get-AgentBridgeOpenClawInputs, Get-AgentBridgeOpenClawRestartDecision, Get-AgentBridgeOpenClawRestartPlan, Save-AgentBridgeOpenClawBaseline, Complete-AgentBridgeOpenClawWarmup
