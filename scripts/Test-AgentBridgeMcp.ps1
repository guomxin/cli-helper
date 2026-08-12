[CmdletBinding()]
param(
    [ValidateSet(
        "SessionStatus",
        "TaihuaSessionStatus",
        "SmartlightSessionStatus",
        "SmartlightOverview",
        "SmartlightLampPosts",
        "SmartlightAlarms",
        "SmartlightInspectionTasks",
        "SmartlightInspectionRunning",
        "SmartlightLeakage",
        "SmartlightCabinets",
        "SmartlightRtus",
        "SmartlightAssetDetail",
        "SmartlightInspectionDetail",
        "SmartlightAlarmAnalysis",
        "SmartlightLeakageAnalysis",
        "OaPendingRead",
        "OaPendingInspect",
        "CertificateSearch",
        "TaihuaMyLogs",
        "YuqueSessionStatus",
        "YuquePublicBooks",
        "YuqueDocumentCatalog",
        "YuqueDocumentSearch",
        "YuqueDocumentRead",
        "LoginReuse",
        "YuqueLoginReuse",
        "CrossEndpointContext",
        "TaskContinuation",
        "ToolCatalog",
        "Release",
        "WorkflowCollections"
    )]
    [string]$Check = "SessionStatus",
    [string]$ServerName = "agentbridge",
    [string]$CaCertificate = "",
    [string]$OpenClawConfig = "",
    [string]$OpenClawEnvFile = "",
    [string]$IdentityLabel = "",
    [string]$IdentityChannel = "",
    [string]$IdentitySenderId = "",
    [string]$EndpointKey = "",
    [string]$HostTaskId = "",
    [ValidateRange(0, 20)]
    [int]$TaskOrdinal = 0,
    [ValidateSet("", "web", "webchat", "telegram", "openclaw-weixin")]
    [string]$SourceClientType = "web",
    [string]$ExpectedText = "",
    [string]$CertificateName = "",
    [string[]]$CertificateNames = @(),
    [ValidateSet("all", "patent_certificate", "software_copyright_certificate")]
    [string]$CertificateDocumentType = "",
    [string]$YuqueBook = "",
    [string]$YuqueQuery = "AI",
    [string]$YuqueDocument = "",
    [ValidateSet("cabinet", "rtu", "lamppost")]
    [string]$SmartlightAssetType = "rtu",
    [string]$SmartlightAssetId = "",
    [string]$SmartlightTaskId = "",
    [string]$SmartlightDetailDate = "",
    [ValidateRange(0, 100000)]
    [int]$YuqueRowOffset = 0,
    [ValidateRange(1, 500)]
    [int]$YuqueMaxRows = 100,
    [ValidateRange(500, 50000)]
    [int]$YuqueMaxChars = 4000
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
    $config = Get-Content -LiteralPath $OpenClawConfig -Raw -Encoding UTF8 | ConvertFrom-Json
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
$selectedIdentityLabel = $null
if ($server) {
    if ($IdentityLabel -or $IdentityChannel -or $IdentitySenderId) {
        throw "Identity selectors require the AgentBridge OpenClaw plugin configuration"
    }
    $headers = Get-PropertyValue -Object $server -Name "headers"
    $url = Resolve-ConfigString -Value ([string](Get-PropertyValue -Object $server -Name "url"))
    $authorization = Resolve-ConfigString -Value ([string](Get-PropertyValue -Object $headers -Name "Authorization"))
    $timeout = Get-PropertyValue -Object $server -Name "timeout"
}
else {
    $plugins = Get-PropertyValue -Object $config -Name "plugins"
    $entries = Get-PropertyValue -Object $plugins -Name "entries"
    $plugin = Get-PropertyValue -Object $entries -Name "agentbridge-interactions"
    $pluginConfig = Get-PropertyValue -Object $plugin -Name "config"
    $configuredUrl = [string](Get-PropertyValue -Object $pluginConfig -Name "mcpUrl")
    if ([string]::IsNullOrWhiteSpace($configuredUrl)) {
        throw "OpenClaw AgentBridge MCP configuration was not found"
    }
    $url = Resolve-ConfigString -Value $configuredUrl
    $timeout = Get-PropertyValue -Object $pluginConfig -Name "mcpTimeoutSeconds"
    $authorization = $null
    $availableBindings = @()
    $bindings = Get-PropertyValue -Object $pluginConfig -Name "identityBindings"
    foreach ($binding in @($bindings)) {
        $tokenEnv = [string](Get-PropertyValue -Object $binding -Name "tokenEnv")
        if ([string]::IsNullOrWhiteSpace($tokenEnv)) {
            continue
        }
        $token = Resolve-EnvironmentValue -Name $tokenEnv
        if (-not [string]::IsNullOrWhiteSpace($token)) {
            $availableBindings += [pscustomobject]@{
                Binding = $binding
                Token = $token
            }
        }
    }
    if (-not $availableBindings) {
        throw "No active AgentBridge identity binding token was found"
    }
    $matches = @($availableBindings | Where-Object {
        $binding = $_.Binding
        (-not $IdentityLabel -or [string](Get-PropertyValue -Object $binding -Name "label") -ieq $IdentityLabel) -and
        (-not $IdentityChannel -or [string](Get-PropertyValue -Object $binding -Name "channel") -ieq $IdentityChannel) -and
        (-not $IdentitySenderId -or [string](Get-PropertyValue -Object $binding -Name "senderId") -eq $IdentitySenderId)
    })
    if (($IdentityLabel -or $IdentityChannel -or $IdentitySenderId) -and $matches.Count -ne 1) {
        throw "AgentBridge identity selector did not resolve exactly one active binding"
    }
    $selected = if ($IdentityLabel -or $IdentityChannel -or $IdentitySenderId) {
        $matches[0]
    }
    else {
        $availableBindings[0]
    }
    $authorization = "Bearer $($selected.Token)"
    $selectedIdentityLabel = [string](Get-PropertyValue -Object $selected.Binding -Name "label")
}
if (-not $url -or -not $authorization.StartsWith("Bearer ")) {
    throw "Resolved MCP configuration is incomplete"
}
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
    $nodeArguments = @($nodeScript, "--check", $Check, "--server-name", $ServerName)
    if ($selectedIdentityLabel) {
        $nodeArguments += @("--identity-label", $selectedIdentityLabel)
    }
    if ($Check -eq "CertificateSearch" -and $CertificateName) {
        $nodeArguments += @("--certificate-name", $CertificateName)
    }
    if ($Check -eq "CertificateSearch" -and $CertificateNames.Count -gt 0) {
        $namesJson = ConvertTo-Json -InputObject $CertificateNames -Compress
        $namesBase64 = [Convert]::ToBase64String(
            [Text.Encoding]::UTF8.GetBytes($namesJson)
        )
        $nodeArguments += @(
            "--certificate-names-base64",
            $namesBase64
        )
    }
    if ($Check -eq "CertificateSearch" -and $CertificateDocumentType) {
        $nodeArguments += @("--certificate-document-type", $CertificateDocumentType)
    }
    if ($Check -eq "SmartlightAssetDetail") {
        if ([string]::IsNullOrWhiteSpace($SmartlightAssetId)) {
            throw "SmartlightAssetId is required for the selected check"
        }
        $nodeArguments += @(
            "--smartlight-asset-type", $SmartlightAssetType,
            "--smartlight-asset-id", $SmartlightAssetId
        )
    }
    if ($Check -eq "SmartlightInspectionDetail") {
        if ([string]::IsNullOrWhiteSpace($SmartlightTaskId)) {
            throw "SmartlightTaskId is required for the selected check"
        }
        $nodeArguments += @("--smartlight-task-id", $SmartlightTaskId)
        if ($SmartlightDetailDate) {
            $nodeArguments += @("--smartlight-detail-date", $SmartlightDetailDate)
        }
    }
    if ($Check -in @("YuqueDocumentCatalog", "YuqueDocumentSearch", "YuqueDocumentRead") -and $YuqueBook) {
        $nodeArguments += @("--yuque-book", $YuqueBook)
    }
    if ($Check -eq "YuqueDocumentSearch") {
        $nodeArguments += @("--yuque-query", $YuqueQuery)
    }
    if ($Check -eq "YuqueDocumentRead") {
        $nodeArguments += @(
            "--yuque-document",
            $YuqueDocument,
            "--yuque-row-offset",
            [string]$YuqueRowOffset,
            "--yuque-max-rows",
            [string]$YuqueMaxRows,
            "--yuque-max-chars",
            [string]$YuqueMaxChars
        )
    }
    if ($Check -in @("CrossEndpointContext", "TaskContinuation")) {
        if ([string]::IsNullOrWhiteSpace($EndpointKey)) {
            throw "EndpointKey is required for the selected host check"
        }
        $nodeArguments += @("--endpoint-key", $EndpointKey)
    }
    if ($Check -eq "TaskContinuation") {
        $nodeArguments += @(
            "--task-ordinal",
            [string]$TaskOrdinal,
            "--source-client-type",
            $SourceClientType
        )
    }
    if ($Check -eq "CrossEndpointContext") {
        if ($ExpectedText) {
            $nodeArguments += @("--expected-text", $ExpectedText)
        }
    }
    if ($HostTaskId) {
        $nodeArguments += @("--host-task-id", $HostTaskId)
    }
    $serverJson | & $node.Source @nodeArguments
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
