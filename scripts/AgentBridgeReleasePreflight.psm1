Set-StrictMode -Version Latest

function Assert-AgentBridgeReleaseCompatibility {
    param([string]$CurrentRelease, [string]$CandidateRelease, [string]$PolicyPath,
          [switch]$ResumeAcceptance)
    if ($CurrentRelease -notmatch '^[0-9a-f]{12}$' -or $CandidateRelease -notmatch '^[0-9a-f]{12}$') {
        throw 'Release preflight could not establish exact current and candidate release IDs'
    }
    if ($CurrentRelease -eq $CandidateRelease) {
        return [pscustomobject]@{ status = 'already_current'; currentRelease = $CurrentRelease; candidateRelease = $CandidateRelease }
    }
    if ($ResumeAcceptance) {
        throw "Release preflight: acceptance candidate $CandidateRelease is not current ($CurrentRelease)"
    }
    $policy = Get-Content -LiteralPath $PolicyPath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
    if ($policy.schemaVersion -ne 'agentbridge.release-policy.v1' -or
        $policy.dataCompatibility -notin @('no-migration', 'reviewed-schema-transition') -or
        $CurrentRelease -cnotin @($policy.compatibleFrom)) {
        throw "Release preflight: deployed $CurrentRelease is not an authorized predecessor of $CandidateRelease. Review deploy/release-policy.json before running validation."
    }
    if ($policy.dataCompatibility -eq 'reviewed-schema-transition' -and
        (-not $policy.PSObject.Properties['schemaTransitions'] -or @($policy.schemaTransitions.PSObject.Properties).Count -eq 0)) {
        throw 'Reviewed schema transition requires explicit before/after schema fingerprints'
    }
    return [pscustomobject]@{ status = 'compatible'; currentRelease = $CurrentRelease; candidateRelease = $CandidateRelease }
}

function Get-AgentBridgeReleasePreflight {
    param([string]$HostName, [string]$SshUser, [string]$RemoteRoot,
          [string]$IdentityFile, [string]$KnownHostsFile,
          [string]$CandidateRelease, [string]$PolicyPath, [switch]$ResumeAcceptance)
    if ($HostName -notmatch '^[A-Za-z0-9.-]+$' -or $SshUser -notmatch '^[A-Za-z0-9._-]+$' -or
        $RemoteRoot -notmatch '^/home/[A-Za-z0-9._/-]+$' -or $RemoteRoot.Contains('..')) {
        throw 'Release preflight target is invalid'
    }
    foreach ($file in @($IdentityFile, $KnownHostsFile, $PolicyPath)) {
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Release preflight file missing: $file" }
    }
    # Read only the release identifier, never emit other environment values.
    $command = "sed -n 's/^AGENTBRIDGE_RELEASE_ID=//p' '$RemoteRoot/config/release.env'"
    $current = (& ssh -i $IdentityFile -o IdentitiesOnly=yes -o BatchMode=yes `
        -o ConnectTimeout=15 -o ConnectionAttempts=1 -o ServerAliveInterval=5 -o ServerAliveCountMax=2 `
        -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$KnownHostsFile" `
        "$SshUser@$HostName" $command | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Release preflight SSH read failed; validation was not started' }
    Assert-AgentBridgeReleaseCompatibility -CurrentRelease $current -CandidateRelease $CandidateRelease `
        -PolicyPath $PolicyPath -ResumeAcceptance:$ResumeAcceptance
}

Export-ModuleMember -Function Get-AgentBridgeReleasePreflight, Assert-AgentBridgeReleaseCompatibility
