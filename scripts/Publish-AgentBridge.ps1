[CmdletBinding()]
param(
    [string]$RemoteName = "origin",
    [string]$BranchName = "main",
    [string]$ExpectedRemoteUrl = "git@github.com:guomxin/cli-helper.git",
    [string]$GitHubIdentityFile = "",
    [string]$GitHubKnownHostsFile = "",
    [string]$AgentBridgeIdentityFile = "",
    [string]$AgentBridgeKnownHostsFile = "",
    [string[]]$IdentityLabel = @(),
    [switch]$SkipValidation,
    [switch]$SkipOpenClawAcceptance,
    [switch]$RestartOpenClaw,
    [switch]$IncludeLoginReuseSmoke,
    [switch]$PlanOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$gitDir = Join-Path $repoRoot ".gitrepo"
$deployScript = Join-Path $PSScriptRoot "Deploy-AgentBridge.ps1"
$validationScript = Join-Path $PSScriptRoot "Invoke-AgentBridgeValidation.ps1"
$releaseAcceptanceScript = Join-Path $PSScriptRoot "Test-AgentBridgeReleaseAcceptance.ps1"
$gitArguments = @("--git-dir=$gitDir", "--work-tree=$repoRoot")

function Invoke-GitRead {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$FailureMessage
    )

    $result = ((& git @gitArguments @Arguments) | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw $FailureMessage
    }
    return $result
}

function Assert-PrivateKeyReadable {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Label SSH private key was not found"
    }
    $sshKeygen = Get-Command ssh-keygen.exe -ErrorAction SilentlyContinue
    if (-not $sshKeygen) { $sshKeygen = Get-Command ssh-keygen -ErrorAction Stop }
    & $sshKeygen.Source -y -f (Resolve-Path $Path).Path *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "$Label SSH private key is not readable by child processes. Run this release entry outside the filesystem sandbox."
    }
}

if (-not (Test-Path -LiteralPath $gitDir -PathType Container)) {
    throw "The repository-local .gitrepo directory was not found"
}
if (-not (Test-Path -LiteralPath $deployScript -PathType Leaf)) {
    throw "The AgentBridge deployment script was not found"
}
if (-not (Test-Path -LiteralPath $validationScript -PathType Leaf)) {
    throw "The AgentBridge validation script was not found"
}
if (-not (Test-Path -LiteralPath $releaseAcceptanceScript -PathType Leaf)) {
    throw "The AgentBridge release-acceptance script was not found"
}
if ($RemoteName -notmatch '^[A-Za-z0-9._-]+$') {
    throw "RemoteName contains unsupported characters"
}
if ($BranchName -notmatch '^[A-Za-z0-9._/-]+$' -or $BranchName.Contains("..")) {
    throw "BranchName contains unsupported characters"
}

$currentBranch = Invoke-GitRead `
    -Arguments @("symbolic-ref", "--short", "HEAD") `
    -FailureMessage "Unable to resolve the current branch"
if ($currentBranch -ne $BranchName) {
    throw "Release branch mismatch: expected $BranchName, found $currentBranch"
}
$remoteUrl = Invoke-GitRead `
    -Arguments @("remote", "get-url", "--push", $RemoteName) `
    -FailureMessage "Unable to resolve the GitHub push remote"
if ($remoteUrl -ne $ExpectedRemoteUrl) {
    throw "Release remote mismatch: expected $ExpectedRemoteUrl, found $remoteUrl"
}
$commit = Invoke-GitRead `
    -Arguments @("rev-parse", "HEAD") `
    -FailureMessage "Unable to resolve the release commit"
if ($commit -notmatch '^[0-9a-f]{40}$') {
    throw "The release commit is invalid"
}
$trackedChanges = Invoke-GitRead `
    -Arguments @("status", "--porcelain", "--untracked-files=no") `
    -FailureMessage "Unable to inspect the repository state"
$isDirty = [bool]$trackedChanges

if (-not $GitHubIdentityFile) {
    $GitHubIdentityFile = Join-Path $env:USERPROFILE ".ssh\id_ed25519_guomxin"
}
if (-not $GitHubKnownHostsFile) {
    $GitHubKnownHostsFile = Join-Path $repoRoot "deploy\ssh\github_known_hosts"
}
if (-not $AgentBridgeIdentityFile) {
    $AgentBridgeIdentityFile = Join-Path $env:USERPROFILE ".ssh\id_ed25519_10_10_50_213"
}
if (-not $AgentBridgeKnownHostsFile) {
    $AgentBridgeKnownHostsFile = Join-Path $repoRoot "deploy\ssh\agentbridge_known_hosts"
}

$plan = [ordered]@{
    status = "planned"
    commit = $commit
    trackedFilesClean = -not $isDirty
    branch = $BranchName
    remote = $RemoteName
    remoteUrl = $remoteUrl
    validation = -not $SkipValidation
    deployment = "root@10.10.50.213:/home/guomao/agentbridge"
    governanceAcceptance = $true
    openClawAcceptance = -not [bool]$SkipOpenClawAcceptance
    isolationIdentities = @($IdentityLabel)
    restartOpenClaw = [bool]$RestartOpenClaw
    push = "$RemoteName/$BranchName"
}
if ($PlanOnly) {
    $plan | ConvertTo-Json -Compress
    exit 0
}
if ($isDirty) {
    throw "Tracked files are modified. Commit the tested candidate before publishing."
}

foreach ($knownHosts in @($GitHubKnownHostsFile, $AgentBridgeKnownHostsFile)) {
    if (-not (Test-Path -LiteralPath $knownHosts -PathType Leaf)) {
        throw "SSH known-hosts file was not found: $knownHosts"
    }
}
$githubKnownHostsText = [IO.File]::ReadAllText($GitHubKnownHostsFile)
if ($githubKnownHostsText -notmatch '(?m)^github\.com ssh-ed25519 ') {
    throw "The tracked GitHub ED25519 host key is missing"
}

# Fail before validation or deployment when Codex launched this entry inside its filesystem sandbox.
Assert-PrivateKeyReadable -Path $GitHubIdentityFile -Label "GitHub"
Assert-PrivateKeyReadable -Path $AgentBridgeIdentityFile -Label "AgentBridge"

if (-not $SkipValidation) {
    & $validationScript -Mode Full
    if ($LASTEXITCODE -ne 0) {
        throw "Full AgentBridge validation failed"
    }
}

$deployParameters = @{
    IdentityFile = (Resolve-Path $AgentBridgeIdentityFile).Path
    KnownHostsFile = (Resolve-Path $AgentBridgeKnownHostsFile).Path
    SkipValidation = $true
}
if ($RestartOpenClaw) { $deployParameters["RestartOpenClaw"] = $true }
if ($IncludeLoginReuseSmoke) { $deployParameters["IncludeLoginReuseSmoke"] = $true }

& $deployScript @deployParameters
if ($LASTEXITCODE -ne 0) {
    throw "AgentBridge deployment failed; GitHub push was not attempted"
}

$acceptanceParameters = @{
    IdentityFile = (Resolve-Path $AgentBridgeIdentityFile).Path
    KnownHostsFile = (Resolve-Path $AgentBridgeKnownHostsFile).Path
}
if ($SkipOpenClawAcceptance) {
    $acceptanceParameters["SkipOpenClaw"] = $true
}
if ($IdentityLabel.Count -gt 0) {
    $acceptanceParameters["IdentityLabel"] = $IdentityLabel
}
& $releaseAcceptanceScript @acceptanceParameters
if ($LASTEXITCODE -ne 0) {
    throw "AgentBridge governance and release acceptance failed; GitHub push was not attempted"
}

$githubIdentityPath = (Resolve-Path $GitHubIdentityFile).Path.Replace("\", "/")
$githubKnownHostsPath = (Resolve-Path $GitHubKnownHostsFile).Path.Replace("\", "/")
$gitSshCommand = "ssh -i `"$githubIdentityPath`" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=15 -o UserKnownHostsFile=`"$githubKnownHostsPath`""
$hadGitSshCommand = Test-Path Env:GIT_SSH_COMMAND
$previousGitSshCommand = $env:GIT_SSH_COMMAND
try {
    $env:GIT_SSH_COMMAND = $gitSshCommand
    & git @gitArguments push --porcelain $RemoteName "HEAD:refs/heads/$BranchName"
    if ($LASTEXITCODE -ne 0) {
        throw "GitHub push failed"
    }
    $remoteLine = Invoke-GitRead `
        -Arguments @("ls-remote", "--exit-code", $RemoteName, "refs/heads/$BranchName") `
        -FailureMessage "Unable to verify the GitHub branch after push"
}
finally {
    if ($hadGitSshCommand) {
        $env:GIT_SSH_COMMAND = $previousGitSshCommand
    }
    else {
        Remove-Item Env:GIT_SSH_COMMAND -ErrorAction SilentlyContinue
    }
}

$remoteCommit = ($remoteLine -split '\s+')[0]
if ($remoteCommit -ne $commit) {
    throw "GitHub verification mismatch: local $commit, remote $remoteCommit"
}

[ordered]@{
    status = "succeeded"
    commit = $commit
    deployed = $true
    deployment = "root@10.10.50.213:/home/guomao/agentbridge"
    pushed = $true
    github = "$RemoteName/$BranchName"
} | ConvertTo-Json -Compress
