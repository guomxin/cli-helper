[CmdletBinding()]
param(
    [string]$HostName = "10.10.50.213",
    [string]$SshUser = "root",
    [string]$IdentityFile = "",
    [string]$RemoteRoot = "/home/guomao/agentbridge",
    [string]$ServiceName = "agentbridge",
    [string]$VenvPath = "",
    [switch]$SkipValidation,
    [switch]$SkipSmoke,
    [switch]$IncludeLoginReuseSmoke,
    [switch]$RestartOpenClaw,
    [switch]$AllowDirty,
    [switch]$PlanOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$validationScript = Join-Path $PSScriptRoot "Invoke-AgentBridgeValidation.ps1"
$smokeScript = Join-Path $PSScriptRoot "Test-AgentBridgeMcp.ps1"

if ($HostName -notmatch '^[A-Za-z0-9.-]+$') {
    throw "HostName contains unsupported characters"
}
if ($SshUser -notmatch '^[A-Za-z0-9._-]+$') {
    throw "SshUser contains unsupported characters"
}
if ($ServiceName -notmatch '^[A-Za-z0-9_.@-]+$') {
    throw "ServiceName contains unsupported characters"
}
if ($RemoteRoot -notmatch '^/home/[A-Za-z0-9._/-]+$' -or $RemoteRoot.Contains("..")) {
    throw "RemoteRoot must be a fixed path below /home"
}

if (-not $IdentityFile) {
    $IdentityFile = Join-Path $env:USERPROFILE ".ssh\id_ed25519_10_10_50_213"
}
if (-not $VenvPath) {
    $cacheRoot = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { $env:USERPROFILE }
    $VenvPath = Join-Path $cacheRoot "AgentBridge\test-venv-py312"
}
$VenvPath = [IO.Path]::GetFullPath($VenvPath)
$venvPython = Join-Path $VenvPath "Scripts\python.exe"

$gitDir = if (Test-Path -LiteralPath (Join-Path $repoRoot ".gitrepo")) {
    Join-Path $repoRoot ".gitrepo"
} else {
    Join-Path $repoRoot ".git"
}
$gitArguments = @("--git-dir=$gitDir", "--work-tree=$repoRoot")
$commit = ((& git @gitArguments rev-parse --short=12 HEAD) | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $commit -notmatch '^[0-9a-f]{7,12}$') {
    throw "Unable to resolve the repository commit"
}
$trackedChanges = ((& git @gitArguments status --porcelain --untracked-files=no) | Out-String).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect the repository state"
}
$isDirty = [bool]$trackedChanges
if ($isDirty -and -not $AllowDirty -and -not $PlanOnly) {
    throw "Tracked files are modified. Commit them first or use -AllowDirty for a development-only deployment."
}
$releaseId = if ($isDirty) { "$commit-dirty" } else { $commit }

$plan = [ordered]@{
    status = "planned"
    releaseId = $releaseId
    target = "$SshUser@$HostName"
    remoteRoot = $RemoteRoot
    validation = -not $SkipValidation
    smoke = -not $SkipSmoke
    loginReuseSmoke = [bool]$IncludeLoginReuseSmoke
    restartOpenClaw = [bool]$RestartOpenClaw
}
if ($PlanOnly) {
    $plan | ConvertTo-Json -Compress
    exit 0
}

if (-not (Test-Path -LiteralPath $IdentityFile -PathType Leaf)) {
    throw "SSH identity file was not found"
}

if (-not $SkipValidation) {
    $validationParameters = @{
        Mode = "Full"
        VenvPath = $VenvPath
    }
    if (-not $RestartOpenClaw) {
        $validationParameters["SkipOpenClaw"] = $true
    }
    & $validationScript @validationParameters
}

if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    throw "Persistent validation environment is missing; run validation first"
}

$releaseDirectory = Join-Path $repoRoot ("output\release\{0}-{1}" -f $releaseId, (Get-Date -Format "yyyyMMddHHmmss"))
New-Item -ItemType Directory -Force -Path $releaseDirectory | Out-Null
& $venvPython -m pip wheel --disable-pip-version-check --no-deps --no-build-isolation --wheel-dir $releaseDirectory $repoRoot
if ($LASTEXITCODE -ne 0) {
    throw "Building the AgentBridge wheel failed"
}
$wheel = Get-ChildItem -LiteralPath $releaseDirectory -Filter "cli_helper-*.whl" -File | Select-Object -Last 1
if (-not $wheel) {
    throw "The AgentBridge wheel was not produced"
}
if ($wheel.Name -notmatch '^[A-Za-z0-9_.+-]+\.whl$') {
    throw "The AgentBridge wheel filename is unsafe"
}

$ssh = Get-Command ssh.exe -ErrorAction SilentlyContinue
if (-not $ssh) { $ssh = Get-Command ssh -ErrorAction Stop }
$scp = Get-Command scp.exe -ErrorAction SilentlyContinue
if (-not $scp) { $scp = Get-Command scp -ErrorAction Stop }
$connectionArguments = @(
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=15",
    "-i", (Resolve-Path $IdentityFile).Path
)
$target = "$SshUser@$HostName"
$remoteWheel = "/tmp/$($wheel.Name)"
$remoteDestination = $target + ":" + $remoteWheel

& $scp.Source @connectionArguments $wheel.FullName $remoteDestination
if ($LASTEXITCODE -ne 0) {
    throw "Uploading the AgentBridge wheel failed"
}

$remoteTemplate = @(
    'set -euo pipefail',
    'wheel=''__REMOTE_WHEEL__''',
    'root=''__REMOTE_ROOT__''',
    'release_id=''__RELEASE_ID__''',
    'service=''__SERVICE_NAME__''',
    'python="$root/venv/bin/python"',
    'release_dir="$root/releases/$release_id"',
    'release_wheel="$release_dir/__WHEEL_NAME__"',
    'trap ''rm -f -- "$wheel"'' EXIT',
    'install -d -m 0750 -o root -g agentbridge "$release_dir"',
    'install -m 0644 -o root -g agentbridge "$wheel" "$release_wheel"',
    '"$python" -m pip install --disable-pip-version-check --no-deps --force-reinstall "$release_wheel"',
    'site_dir="$("$python" -c ''import pathlib, bscli; print(pathlib.Path(bscli.__file__).parent)'')"',
    '"$python" -m compileall -q "$site_dir"',
    '"$python" -m pip check',
    'systemctl restart "$service"',
    'systemctl is-active --quiet "$service"',
    'printf ''{"status":"succeeded","service":"%s","releaseId":"%s"}\n'' "$service" "$release_id"',
    '# agentbridge-upload-end'
) -join "`n"
$remoteScript = $remoteTemplate.Replace("__REMOTE_WHEEL__", $remoteWheel).Replace("__REMOTE_ROOT__", $RemoteRoot).Replace("__RELEASE_ID__", $releaseId).Replace("__SERVICE_NAME__", $ServiceName).Replace("__WHEEL_NAME__", $wheel.Name)
$remoteScript | & $ssh.Source -T @connectionArguments $target "bash -s"
if ($LASTEXITCODE -ne 0) {
    throw "Remote AgentBridge deployment failed"
}

if ($RestartOpenClaw) {
    & openclaw gateway restart
    if ($LASTEXITCODE -ne 0) { throw "OpenClaw Gateway restart failed" }
    $gatewayStatus = (& openclaw gateway status --deep --require-rpc --json) | Out-String | ConvertFrom-Json
    if (-not $gatewayStatus.rpc.ok) { throw "OpenClaw Gateway deep RPC check failed" }
    $plugin = (& openclaw plugins inspect agentbridge-interactions --runtime --json) | Out-String | ConvertFrom-Json
    if ($plugin.plugin.status -ne "loaded") { throw "AgentBridge OpenClaw plugin is not loaded" }
}

if (-not $SkipSmoke) {
    & $smokeScript -Check SessionStatus
    if ($IncludeLoginReuseSmoke) {
        & $smokeScript -Check LoginReuse
    }
}

[ordered]@{
    status = "succeeded"
    releaseId = $releaseId
    target = $target
    wheel = $wheel.FullName
    service = $ServiceName
    smoke = -not $SkipSmoke
    restartOpenClaw = [bool]$RestartOpenClaw
} | ConvertTo-Json -Compress