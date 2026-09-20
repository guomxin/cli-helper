Set-StrictMode -Version Latest

function Read-AgentBridgeEnvironment {
    param([string]$Path, [string]$RepoRoot, [string]$HostName, [string]$RemoteRoot)
    if (-not $Path) { throw 'Explicit -EnvironmentProfile or AGENTBRIDGE_ENVIRONMENT_PROFILE is required' }
    $profilePath = (Resolve-Path -LiteralPath $Path -ErrorAction Stop).Path
    $repoPath = [IO.Path]::GetFullPath($RepoRoot).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    if ($profilePath.StartsWith($repoPath, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Real environment profiles must be stored outside the public repository'
    }
    $profile = Get-Content -LiteralPath $profilePath -Raw | ConvertFrom-Json
    if ($profile.schema -ne 'agentbridge.environment.v1' -or $profile.host -ne $HostName -or $profile.root -ne $RemoteRoot) {
        throw 'Environment profile does not match the explicitly selected release target'
    }
    $units = @{}
    foreach ($name in @('agentbridge.service', 'agentbridge-backup.service', 'agentbridge-backup.timer')) {
        $entry = $profile.units.$name
        $unitPath = (Resolve-Path -LiteralPath $entry.path -ErrorAction Stop).Path
        if ($unitPath.StartsWith($repoPath, [StringComparison]::OrdinalIgnoreCase)) { throw 'Runtime units must be outside the public repository' }
        $hash = (Get-FileHash -LiteralPath $unitPath -Algorithm SHA256).Hash.ToLower()
        if ($entry.sha256 -cne $hash) { throw "Environment unit changed without a reviewed profile update: $name" }
        $units[$name] = @{ Path = $unitPath; Hash = $hash; Bytes = [IO.File]::ReadAllBytes($unitPath) }
        # Pin the bytes used by this invocation, including changes concurrent with the read.
        $hasher = [Security.Cryptography.SHA256]::Create()
        try { $actual = [BitConverter]::ToString($hasher.ComputeHash($units[$name].Bytes)).Replace('-', '').ToLower() }
        finally { $hasher.Dispose() }
        if ($actual -cne $hash) { throw "Environment unit changed while reading: $name" }
    }
    return @{ Path = $profilePath; Units = $units }
}

Export-ModuleMember -Function Read-AgentBridgeEnvironment
