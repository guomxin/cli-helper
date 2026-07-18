[CmdletBinding()]
param(
    [ValidateSet("SessionStatus", "LoginReuse", "Release")]
    [string]$Check = "SessionStatus",
    [string]$ServerName = "agentbridge",
    [string]$CaCertificate = "",
    [string]$OpenClawConfig = "",
    [string]$OpenClawEnvFile = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$nodeScript = Join-Path $PSScriptRoot "agentbridge-mcp-smoke.mjs"
$node = Get-Command node.exe -ErrorAction SilentlyContinue
if (-not $node) {
    $node = Get-Command node -ErrorAction Stop
}

if (-not $OpenClawConfig) {
    $OpenClawConfig = Join-Path $env:USERPROFILE ".openclaw\openclaw.json"
}
if (-not $OpenClawEnvFile) {
    $OpenClawEnvFile = Join-Path $env:USERPROFILE ".openclaw\.env"
}
if (-not (Test-Path -LiteralPath $OpenClawConfig -PathType Leaf)) {
    throw "OpenClaw configuration was not found"
}
try {
    $config = Get-Content -LiteralPath $OpenClawConfig -Raw | ConvertFrom-Json
}
catch {
    throw "OpenClaw configuration is invalid JSON"
}

$environment = @{}
if (Test-Path -LiteralPath $OpenClawEnvFile -PathType Leaf) {
    foreach ($line in Get-Content -LiteralPath $OpenClawEnvFile) {
        if ($line -notmatch '^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$') {
            continue
        }
        $name = $Matches[1]
        $value = $Matches[2].Trim()
        if ($value.Length -ge 2) {
            $first = $value[0]
            $last = $value[$value.Length - 1]
            if (($first -eq '"' -and $last -eq '"') -or ($first -eq "'" -and $last -eq "'")) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }
        $environment[$name] = $value
    }
}

function Get-PropertyValue {
    param(
        [AllowNull()][object]$Object,
        [Parameter(Mandatory = $true)][string]$Name
    )

    if ($null -eq $Object) {
        return $null
    }
    $property = $Object.PSObject.Properties | Where-Object { $_.Name -ieq $Name } | Select-Object -First 1
    if ($property) {
        return $property.Value
    }
    return $null
}

function Resolve-EnvironmentValue {
    param([Parameter(Mandatory = $true)][string]$Name)

    foreach ($scope in @("Process", "User", "Machine")) {
        $value = [Environment]::GetEnvironmentVariable($Name, $scope)
        if (-not [string]::IsNullOrWhiteSpace($value)) {
            return $value
        }
    }
    if ($environment.ContainsKey($Name) -and -not [string]::IsNullOrWhiteSpace([string]$environment[$Name])) {
        return [string]$environment[$Name]
    }
    return $null
}

function Resolve-ConfigString {
    param([Parameter(Mandatory = $true)][string]$Value)

    $resolved = $Value
    foreach ($match in [regex]::Matches($Value, '\$\{([A-Za-z_][A-Za-z0-9_]*)\}')) {
        $replacement = Resolve-EnvironmentValue -Name $match.Groups[1].Value
        if ([string]::IsNullOrWhiteSpace($replacement)) {
            throw "OpenClaw MCP configuration references an unavailable environment variable"
        }
        $resolved = $resolved.Replace($match.Value, $replacement)
    }
    return $resolved
}

$servers = Get-PropertyValue -Object (Get-PropertyValue -Object $config -Name "mcp") -Name "servers"
$server = Get-PropertyValue -Object $servers -Name $ServerName
if (-not $server) {
    throw "OpenClaw MCP server was not found: $ServerName"
}
$headers = Get-PropertyValue -Object $server -Name "headers"
$url = Resolve-ConfigString -Value ([string](Get-PropertyValue -Object $server -Name "url"))
$authorization = Resolve-ConfigString -Value ([string](Get-PropertyValue -Object $headers -Name "Authorization"))
if (-not $url -or -not $authorization.StartsWith("Bearer ")) {
    throw "Resolved MCP configuration is incomplete"
}
$timeout = Get-PropertyValue -Object $server -Name "timeout"
$resolvedServer = [ordered]@{
    url = $url
    timeout = if ($null -ne $timeout) { [int]$timeout } else { 60 }
    headers = @{ Authorization = $authorization }
}
$serverJson = $resolvedServer | ConvertTo-Json -Compress -Depth 5

if (-not $CaCertificate) {
    if ($env:NODE_EXTRA_CA_CERTS) {
        $CaCertificate = $env:NODE_EXTRA_CA_CERTS
    }
    else {
        $configEnvironment = Get-PropertyValue -Object (Get-PropertyValue -Object $config -Name "env") -Name "vars"
        $configuredCa = Get-PropertyValue -Object $configEnvironment -Name "NODE_EXTRA_CA_CERTS"
        if ($configuredCa) {
            $CaCertificate = Resolve-ConfigString -Value ([string]$configuredCa)
        }
        else {
            $CaCertificate = Join-Path $env:USERPROFILE ".agentbridge\pki\root-ca.crt"
        }
    }
}
if (-not [IO.Path]::IsPathRooted($CaCertificate)) {
    $CaCertificate = Join-Path (Split-Path $OpenClawConfig -Parent) $CaCertificate
}
if (-not (Test-Path -LiteralPath $CaCertificate -PathType Leaf)) {
    throw "AgentBridge CA certificate was not found"
}

$hadPreviousCa = Test-Path Env:NODE_EXTRA_CA_CERTS
$previousCa = $env:NODE_EXTRA_CA_CERTS
try {
    $env:NODE_EXTRA_CA_CERTS = (Resolve-Path $CaCertificate).Path
    $serverJson | & $node.Source $nodeScript --check $Check --server-name $ServerName
    if ($LASTEXITCODE -ne 0) {
        throw "AgentBridge MCP smoke check failed"
    }
}
finally {
    if ($hadPreviousCa) {
        $env:NODE_EXTRA_CA_CERTS = $previousCa
    }
    else {
        Remove-Item Env:NODE_EXTRA_CA_CERTS -ErrorAction SilentlyContinue
    }
}