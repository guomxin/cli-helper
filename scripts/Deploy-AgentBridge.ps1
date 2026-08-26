[CmdletBinding()]
param(
    [string]$HostName = "10.10.50.213",
    [string]$SshUser = "root",
    [string]$IdentityFile = "",
    [string]$KnownHostsFile = "",
    [string]$RemoteRoot = "/home/guomao/agentbridge",
    [string]$ServiceName = "agentbridge",
    [string]$VenvPath = "",
    [switch]$SkipValidation,
    [switch]$SkipSmoke,
    [switch]$IncludeLoginReuseSmoke,
    [switch]$RestartOpenClaw,
    [switch]$InstallSystemDependencies,
    [switch]$AllowDirty,
    [switch]$PlanOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$validationScript = Join-Path $PSScriptRoot "Invoke-AgentBridgeValidation.ps1"
$smokeScript = Join-Path $PSScriptRoot "Test-AgentBridgeMcp.ps1"
$gatewayWarmupScript = Join-Path $PSScriptRoot "Test-OpenClawGatewayWarmup.ps1"
$gatewayLifecycleScript = Join-Path $PSScriptRoot "Restart-AgentBridgeOpenClawGateway.ps1"
$gatewayRuntimeScript = Join-Path $PSScriptRoot "Test-AgentBridgeOpenClawRuntime.ps1"

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
$systemdUnit = Join-Path $repoRoot "deploy\systemd\$ServiceName.service"
$backupSystemdUnit = Join-Path $repoRoot "deploy\systemd\$ServiceName-backup.service"
$backupSystemdTimer = Join-Path $repoRoot "deploy\systemd\$ServiceName-backup.timer"
if (-not (Test-Path -LiteralPath $systemdUnit -PathType Leaf)) {
    throw "Version-controlled systemd unit was not found: $systemdUnit"
}
if (-not (Test-Path -LiteralPath $backupSystemdUnit -PathType Leaf)) {
    throw "Version-controlled backup systemd unit was not found: $backupSystemdUnit"
}
if (-not (Test-Path -LiteralPath $backupSystemdTimer -PathType Leaf)) {
    throw "Version-controlled backup systemd timer was not found: $backupSystemdTimer"
}

if (-not $IdentityFile) {
    $IdentityFile = Join-Path $env:USERPROFILE ".ssh\id_ed25519_10_10_50_213"
}
if (-not $KnownHostsFile) {
    $KnownHostsFile = Join-Path $repoRoot "deploy\ssh\agentbridge_known_hosts"
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
    openClawGuardrails = [bool]$RestartOpenClaw
    openClawWarmup = [bool]$RestartOpenClaw
    installSystemDependencies = [bool]$InstallSystemDependencies
    systemdUnits = @($systemdUnit, $backupSystemdUnit, $backupSystemdTimer)
}
if ($PlanOnly) {
    $plan | ConvertTo-Json -Compress
    exit 0
}

if (-not (Test-Path -LiteralPath $IdentityFile -PathType Leaf)) {
    throw "SSH identity file was not found"
}
if (-not (Test-Path -LiteralPath $KnownHostsFile -PathType Leaf)) {
    throw "SSH known-hosts file was not found"
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
$buildDirectory = [IO.Path]::GetFullPath((Join-Path $repoRoot "build"))
$buildParent = [IO.Directory]::GetParent($buildDirectory)
if ($null -eq $buildParent -or -not [String]::Equals(
    $buildParent.FullName, [IO.Path]::GetFullPath($repoRoot),
    [StringComparison]::OrdinalIgnoreCase
)) {
    throw "Refusing to clean a build directory outside the repository"
}
if (Test-Path -LiteralPath $buildDirectory) {
    Remove-Item -LiteralPath $buildDirectory -Recurse -Force
}
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
    "-o", "UserKnownHostsFile=$((Resolve-Path $KnownHostsFile).Path)",
    "-i", (Resolve-Path $IdentityFile).Path
)
$target = "$SshUser@$HostName"
$remoteWheel = "/tmp/$($wheel.Name)"
$remoteDestination = $target + ":" + $remoteWheel
$systemdUnitBase64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($systemdUnit))
$backupSystemdUnitBase64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($backupSystemdUnit))
$backupSystemdTimerBase64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($backupSystemdTimer))

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
    'unit_tmp_dir="/tmp/agentbridge-systemd-$release_id"',
    'unit_tmp="$unit_tmp_dir/$service.service"',
    'backup_unit_tmp="$unit_tmp_dir/$service-backup.service"',
    'backup_timer_tmp="$unit_tmp_dir/$service-backup.timer"',
    'unit_path="/etc/systemd/system/$service.service"',
    'backup_unit_path="/etc/systemd/system/$service-backup.service"',
    'backup_timer_path="/etc/systemd/system/$service-backup.timer"',
    'unit_b64=''__SYSTEMD_UNIT_BASE64__''',
    'backup_unit_b64=''__BACKUP_SYSTEMD_UNIT_BASE64__''',
    'backup_timer_b64=''__BACKUP_SYSTEMD_TIMER_BASE64__''',
    'install_system_dependencies=''__INSTALL_SYSTEM_DEPENDENCIES__''',
    'trap ''rm -f -- "$wheel"; rm -rf -- "$unit_tmp_dir"'' EXIT',
    'if [ "$install_system_dependencies" = "1" ]; then DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends xvfb x11vnc novnc websockify xauth; fi',
    'for command in Xvfb x11vnc websockify xauth; do command -v "$command" >/dev/null || { printf ''%s is required; deploy once with -InstallSystemDependencies\n'' "$command" >&2; exit 1; }; done',
    'test -d /usr/share/novnc || { printf ''noVNC web root is required; deploy once with -InstallSystemDependencies\n'' >&2; exit 1; }',
    'install -d -m 0700 "$unit_tmp_dir"',
    'install -d -m 0750 -o root -g agentbridge "$release_dir"',
    'install -d -m 0700 -o agentbridge -g agentbridge "$root/backups"',
    'install -m 0644 -o root -g agentbridge "$wheel" "$release_wheel"',
    '"$python" -m pip install --disable-pip-version-check --no-deps --force-reinstall "$release_wheel"',
    'site_dir="$(cd / && "$python" -P -c ''import pathlib, bscli; print(pathlib.Path(bscli.__file__).parent.resolve())'')"',
    'case "$site_dir" in "$root"/venv/lib/python*/site-packages/bscli) ;; *) printf ''unexpected installed bscli path: %s\n'' "$site_dir" >&2; exit 1 ;; esac',
    '"$python" -m compileall -q "$site_dir"',
    '"$python" -m pip check',
    'printf ''%s'' "$unit_b64" | base64 --decode > "$unit_tmp"',
    'printf ''%s'' "$backup_unit_b64" | base64 --decode > "$backup_unit_tmp"',
    'printf ''%s'' "$backup_timer_b64" | base64 --decode > "$backup_timer_tmp"',
    'systemd-analyze verify "$unit_tmp" "$backup_unit_tmp" "$backup_timer_tmp"',
    'install -m 0644 -o root -g root "$unit_tmp" "$unit_path"',
    'install -m 0644 -o root -g root "$backup_unit_tmp" "$backup_unit_path"',
    'install -m 0644 -o root -g root "$backup_timer_tmp" "$backup_timer_path"',
    'install -d -m 0750 -o root -g agentbridge "$root/config"',
    'printf ''AGENTBRIDGE_RELEASE_ID=%s\n'' "$release_id" > "$root/config/release.env"',
    'chown root:agentbridge "$root/config/release.env"',
    'chmod 0640 "$root/config/release.env"',
    'systemctl daemon-reload',
    'systemctl restart "$service"',
    'systemctl disable --now agentbridge-xvfb.service >/dev/null 2>&1 || true',
    'rm -f -- /etc/systemd/system/agentbridge-xvfb.service',
    'systemctl daemon-reload',
    'release_process_ready=0',
    'for attempt in $(seq 1 30); do',
    '  if systemctl is-active --quiet "$service"; then',
    '    main_pid="$(systemctl show "$service" -p MainPID --value)"',
    '    if [ "$main_pid" -gt 0 ] && [ -r "/proc/$main_pid/cmdline" ]; then',
    '      process_cwd="$(readlink "/proc/$main_pid/cwd")"',
    '      if [ "$process_cwd" = "$root" ] && tr ''\0'' ''\n'' < "/proc/$main_pid/cmdline" | grep -Fx -- ''-P'' >/dev/null; then release_process_ready=1; break; fi',
    '    fi',
    '  fi',
    '  sleep 1',
    'done',
    'if [ "$release_process_ready" -ne 1 ]; then printf ''service did not stabilize on the release unit\n'' >&2; exit 1; fi',
    'command -v curl >/dev/null || { printf ''curl is required for deployment readiness checks\n'' >&2; exit 1; }',
    'governance_ready=0',
    'for attempt in $(seq 1 30); do',
    '  if curl --insecure --fail --silent --max-time 3 "https://__HOST_NAME__:8782/readyz" | grep -Eq ''"status"[[:space:]]*:[[:space:]]*"ready"''; then governance_ready=1; break; fi',
    '  sleep 1',
    'done',
    'if [ "$governance_ready" -ne 1 ]; then printf ''service readiness did not stabilize before backup\n'' >&2; exit 1; fi',
    'runtime_module="$(cd "$root" && runuser -u agentbridge -- env HOME="$root/data" AGENTBRIDGE_SESSION_KEY_FILE="$root/config/session.key" "$python" -P -c ''import pathlib, bscli; print(pathlib.Path(bscli.__file__).resolve())'')"',
    'case "$runtime_module" in "$site_dir"/*) ;; *) printf ''service resolves unexpected bscli module: %s\n'' "$runtime_module" >&2; exit 1 ;; esac',
    'systemctl enable --now "$service-backup.timer"',
    'systemctl start "$service-backup.service"',
    'printf ''{"status":"succeeded","service":"%s","releaseId":"%s"}\n'' "$service" "$release_id"',
    '# agentbridge-upload-end'
) -join "`n"
$remoteScript = $remoteTemplate.Replace("__REMOTE_WHEEL__", $remoteWheel).Replace("__REMOTE_ROOT__", $RemoteRoot).Replace("__RELEASE_ID__", $releaseId).Replace("__SERVICE_NAME__", $ServiceName).Replace("__HOST_NAME__", $HostName).Replace("__WHEEL_NAME__", $wheel.Name).Replace("__SYSTEMD_UNIT_BASE64__", $systemdUnitBase64).Replace("__BACKUP_SYSTEMD_UNIT_BASE64__", $backupSystemdUnitBase64).Replace("__BACKUP_SYSTEMD_TIMER_BASE64__", $backupSystemdTimerBase64).Replace("__INSTALL_SYSTEM_DEPENDENCIES__", $(if ($InstallSystemDependencies) { "1" } else { "0" }))
$remoteScript | & $ssh.Source -T @connectionArguments $target "bash -s"
if ($LASTEXITCODE -ne 0) {
    throw "Remote AgentBridge deployment failed"
}

if ($RestartOpenClaw) {
    $openClawConfigPath = if ($env:OPENCLAW_CONFIG_PATH) {
        $env:OPENCLAW_CONFIG_PATH
    }
    else {
        Join-Path $env:USERPROFILE ".openclaw\openclaw.json"
    }
    $diagnosticsAlreadyConfigured = $false
    if (Test-Path -LiteralPath $openClawConfigPath -PathType Leaf) {
        try {
            $openClawConfig = Get-Content -LiteralPath $openClawConfigPath `
                -Raw -Encoding utf8 | ConvertFrom-Json
            $diagnosticsAlreadyConfigured = (
                $openClawConfig.diagnostics.stuckSessionWarnMs -eq 30000 -and
                $openClawConfig.diagnostics.stuckSessionAbortMs -eq 120000
            )
        }
        catch {
            $diagnosticsAlreadyConfigured = $false
        }
    }
    $diagnosticsBatch = @(
        @{
            path = "diagnostics.stuckSessionWarnMs"
            value = 30000
        },
        @{
            path = "diagnostics.stuckSessionAbortMs"
            value = 120000
        }
    ) | ConvertTo-Json -Compress
    if ($diagnosticsAlreadyConfigured) {
        Write-Host "OpenClaw stuck-session recovery is already configured; skipping config write."
    }
    else {
        $diagnosticsBatchFile = Join-Path ([IO.Path]::GetTempPath()) (
            "agentbridge-openclaw-diagnostics-{0}.json" -f [guid]::NewGuid().ToString("N")
        )
        $configExitCode = 1
        try {
            [IO.File]::WriteAllText(
                $diagnosticsBatchFile,
                $diagnosticsBatch,
                [Text.UTF8Encoding]::new($false)
            )
            & openclaw config set --batch-file $diagnosticsBatchFile
            $configExitCode = $LASTEXITCODE
        }
        finally {
            Remove-Item -LiteralPath $diagnosticsBatchFile -Force -ErrorAction SilentlyContinue
        }
        if ($configExitCode -ne 0) {
            throw "Configuring OpenClaw stuck-session recovery failed"
        }
    }
    $gatewayRestart = (
        & $gatewayLifecycleScript -ReadyTimeoutSeconds 300
    ) | Out-String | ConvertFrom-Json
    if ($gatewayRestart.status -ne "succeeded" -or
        -not $gatewayRestart.visibleForeground) {
        throw "Visible OpenClaw Gateway restart failed"
    }

    $gatewayRuntime = (
        & $gatewayRuntimeScript -RegistrationMaxAgeSeconds 300
    ) | Out-String | ConvertFrom-Json
    if ($gatewayRuntime.status -ne "succeeded" -or
        $gatewayRuntime.pluginStatus -ne "loaded") {
        throw "OpenClaw Gateway runtime or AgentBridge plugin is not healthy"
    }
    $warmup = (& $gatewayWarmupScript) | Out-String | ConvertFrom-Json
    if ($warmup.status -ne "succeeded") {
        throw "OpenClaw Gateway cold/hot warm-up failed"
    }
}

if (-not $SkipSmoke) {
    $releaseSmokeAttempts = 6
    for ($attempt = 1; $attempt -le $releaseSmokeAttempts; $attempt++) {
        try {
            & $smokeScript -Check Release
            break
        } catch {
            if ($attempt -eq $releaseSmokeAttempts) {
                throw
            }
            Start-Sleep -Seconds 5
        }
    }
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
    installSystemDependencies = [bool]$InstallSystemDependencies
} | ConvertTo-Json -Compress
