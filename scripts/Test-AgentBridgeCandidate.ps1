[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$Commit,
      [Parameter(Mandatory=$true)][string]$IdentityFile,
      [Parameter(Mandatory=$true)][string]$KnownHostsFile,
      [Parameter(Mandatory=$true)][string]$Python)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($Commit -notmatch '^[0-9a-f]{40}$') { throw 'Invalid candidate commit' }
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$branch = "candidate/$Commit"
$previous = $env:GIT_SSH_COMMAND
try {
    $key = (Resolve-Path -LiteralPath $IdentityFile).Path.Replace('\', '/')
    $hosts = (Resolve-Path -LiteralPath $KnownHostsFile).Path.Replace('\', '/')
    $env:GIT_SSH_COMMAND = "ssh -i `"$key`" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=15 -o UserKnownHostsFile=`"$hosts`""
    & git "--git-dir=$root/.gitrepo" "--work-tree=$root" push origin "${Commit}:refs/heads/$branch"
    if ($LASTEXITCODE -ne 0) { throw 'Candidate branch push failed; deployment blocked' }
} finally { $env:GIT_SSH_COMMAND = $previous }
& $Python (Join-Path $PSScriptRoot 'candidate_gate.py') --commit $Commit --branch $branch
if ($LASTEXITCODE -ne 0) { throw 'Candidate CI gate failed; deployment blocked' }
