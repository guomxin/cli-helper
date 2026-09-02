[CmdletBinding()]
param(
    [string]$GatewayLauncher = (
        "$env:LOCALAPPDATA\AgentBridge\openclaw-gateway-runtime.cmd"
    ),
    [ValidateRange(1, 65535)][int]$GatewayPort = 18789,
    [ValidateRange(10, 120)][int]$StopTimeoutSeconds = 30,
    [ValidateRange(30, 900)][int]$ReadyTimeoutSeconds = 300,
    [switch]$StartOnly,
    [switch]$StopOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($StartOnly -and $StopOnly) {
    throw "StartOnly and StopOnly cannot be used together"
}
if (-not (Test-Path -LiteralPath $GatewayLauncher -PathType Leaf)) {
    throw "OpenClaw Gateway launcher was not found: $GatewayLauncher"
}

$foregroundScript = Join-Path $PSScriptRoot "Invoke-AgentBridgeOpenClawGatewayForeground.ps1"
if (-not (Test-Path -LiteralPath $foregroundScript -PathType Leaf)) {
    throw "Foreground Gateway host was not found: $foregroundScript"
}
$lifecycleStateModule = Join-Path `
    $PSScriptRoot `
    "AgentBridgeOpenClawLifecycleLease.psm1"
if (-not (Test-Path -LiteralPath $lifecycleStateModule -PathType Leaf)) {
    throw "OpenClaw lifecycle lease module was not found: $lifecycleStateModule"
}
Import-Module $lifecycleStateModule -Force

$stateRoot = Join-Path $env:LOCALAPPDATA "AgentBridge"
$logRoot = Join-Path $stateRoot "logs"
$statusPath = Join-Path $stateRoot "openclaw-lifecycle-status.json"
$operationPath = Join-Path $stateRoot "openclaw-lifecycle-operation.json"
$logPath = Join-Path $logRoot "openclaw-lifecycle.log"
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
$script:operationId = [guid]::NewGuid().ToString("N")
$script:operationStartedAt = [DateTimeOffset]::UtcNow
$script:operationAction = "unknown"
$script:operationInitialized = $false
$script:operationFinished = $false

function Update-LifecycleLease {
    param(
        [Parameter(Mandatory = $true)][string]$Phase,
        [ValidateSet("active", "completed", "failed")]
        [string]$State = "active",
        [string]$ErrorCode = ""
    )
    if (-not $script:operationInitialized) { return }
    Set-AgentBridgeOpenClawLifecycleLease `
        -Path $operationPath `
        -GatewayPort $GatewayPort `
        -OperationId $script:operationId `
        -Action $script:operationAction `
        -State $State `
        -Phase $Phase `
        -StartedAt $script:operationStartedAt `
        -ErrorCode $ErrorCode | Out-Null
}

function Write-LifecycleLog {
    param([Parameter(Mandatory = $true)][string]$Message)
    $line = "{0} {1}" -f [DateTimeOffset]::UtcNow.ToString("o"), $Message
    Add-Content -LiteralPath $logPath -Value $line -Encoding utf8
}

function Get-GatewayProcesses {
    return @(
        Get-CimInstance Win32_Process -Filter "Name = 'node.exe'" `
            -ErrorAction SilentlyContinue |
            Where-Object {
                $_.CommandLine -match 'openclaw\.mjs.*gateway run' -and
                $_.CommandLine -match "--port\s+$GatewayPort(?:\s|$)"
            }
    )
}

function Get-GatewayListener {
    return @(
        Get-NetTCPConnection -State Listen -LocalPort $GatewayPort `
            -ErrorAction SilentlyContinue |
            Sort-Object OwningProcess -Unique
    )
}

function Get-VisibleGatewayForeground {
    param([Parameter(Mandatory = $true)][int]$GatewayProcessId)

    $currentId = $GatewayProcessId
    for ($depth = 0; $depth -lt 10 -and $currentId -gt 0; $depth++) {
        $current = Get-CimInstance Win32_Process `
            -Filter "ProcessId = $currentId" `
            -ErrorAction SilentlyContinue
        if (-not $current) { return $null }
        if ($current.Name -eq "powershell.exe" -and
            $current.CommandLine -match 'Invoke-AgentBridgeOpenClawGatewayForeground\.ps1') {
            $terminal = Get-Process -Id $current.ParentProcessId `
                -ErrorAction SilentlyContinue |
                Where-Object {
                    $_.ProcessName -eq "WindowsTerminal" -and
                    $_.MainWindowHandle -ne 0
                }
            if (-not $terminal) { return $null }
            return [ordered]@{
                processId = [int]$current.ProcessId
                terminalProcessId = [int]$terminal.Id
            }
        }
        if ([int]$current.ParentProcessId -eq $currentId) { return $null }
        $currentId = [int]$current.ParentProcessId
    }
    return $null
}

function Stop-GatewayProcesses {
    $stopped = @()
    foreach ($process in @(Get-GatewayProcesses)) {
        $current = Get-CimInstance Win32_Process `
            -Filter "ProcessId = $($process.ProcessId)" `
            -ErrorAction SilentlyContinue
        if (-not $current -or $current.Name -ne "node.exe" -or
            $current.CommandLine -notmatch 'openclaw\.mjs.*gateway run' -or
            $current.CommandLine -notmatch "--port\s+$GatewayPort(?:\s|$)") {
            continue
        }
        Stop-Process -Id $current.ProcessId -Force -ErrorAction Stop
        $stopped += [int]$current.ProcessId
    }
    return $stopped
}

function Wait-GatewayStopped {
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($StopTimeoutSeconds)
    do {
        Update-LifecycleLease -Phase "waiting_for_stop"
        if (@(Get-GatewayProcesses).Count -eq 0 -and
            @(Get-GatewayListener).Count -eq 0) {
            return
        }
        Start-Sleep -Milliseconds 500
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw "Timed out waiting for the previous OpenClaw Gateway to stop"
}

function Remove-StaleGatewayLocks {
    if (@(Get-GatewayProcesses).Count -gt 0 -or @(Get-GatewayListener).Count -gt 0) {
        throw "Refusing to inspect Gateway locks while a Gateway is still active"
    }
    $removed = @()
    $lockRoot = Join-Path ([IO.Path]::GetTempPath()) "openclaw"
    if (-not (Test-Path -LiteralPath $lockRoot -PathType Container)) {
        return $removed
    }
    foreach ($lock in @(Get-ChildItem -LiteralPath $lockRoot -Filter "gateway.*.lock" -File)) {
        $metadata = $null
        try {
            $metadata = Get-Content -LiteralPath $lock.FullName -Raw | ConvertFrom-Json
        }
        catch {
            Write-LifecycleLog "Ignored unreadable Gateway lock $($lock.FullName)."
            continue
        }
        if ([int]$metadata.port -ne $GatewayPort) { continue }
        $lockProcess = Get-CimInstance Win32_Process `
            -Filter "ProcessId = $([int]$metadata.pid)" `
            -ErrorAction SilentlyContinue
        if ($lockProcess -and $lockProcess.Name -eq "node.exe" -and
            $lockProcess.CommandLine -match 'openclaw\.mjs.*gateway run' -and
            $lockProcess.CommandLine -match "--port\s+$GatewayPort(?:\s|$)") {
            throw "Gateway lock still belongs to active process $($metadata.pid)"
        }
        Remove-Item -LiteralPath $lock.FullName -Force
        $removed += $lock.FullName
        Write-LifecycleLog (
            "Removed stale Gateway lock {0} for inactive PID {1}." -f `
                $lock.FullName, $metadata.pid
        )
    }
    return $removed
}

function Start-VisibleGateway {
    $existingHostIds = @(
        Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" `
            -ErrorAction SilentlyContinue |
            Where-Object {
                $_.CommandLine -match 'Invoke-AgentBridgeOpenClawGatewayForeground\.ps1'
            } |
            ForEach-Object { [int]$_.ProcessId }
    )
    $resolvedLauncher = (Resolve-Path -LiteralPath $GatewayLauncher).Path
    $terminal = Get-Command wt.exe -ErrorAction Stop
    & $terminal.Source `
        -w AgentBridgeGateway `
        new-tab `
        --title "AgentBridge OpenClaw Gateway" `
        --suppressApplicationTitle `
        powershell.exe `
        -NoLogo `
        -NoProfile `
        -ExecutionPolicy Bypass `
        -File $foregroundScript `
        -GatewayLauncher $resolvedLauncher
    if ($LASTEXITCODE -ne 0) {
        throw "Windows Terminal failed to create the visible Gateway host"
    }

    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(15)
    do {
        Update-LifecycleLease -Phase "starting_visible_host"
        $candidate = Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" `
            -ErrorAction SilentlyContinue |
            Where-Object {
                $_.CommandLine -match 'Invoke-AgentBridgeOpenClawGatewayForeground\.ps1' -and
                [int]$_.ProcessId -notin $existingHostIds
            } |
            Sort-Object CreationDate -Descending |
            Select-Object -First 1
        if ($candidate) {
            $terminalWindow = Get-Process -Id $candidate.ParentProcessId `
                -ErrorAction SilentlyContinue |
                Where-Object {
                    $_.ProcessName -eq 'WindowsTerminal' -and
                    $_.MainWindowHandle -ne 0
                }
            if ($terminalWindow) {
                Write-LifecycleLog (
                    "Started visible Gateway host process {0} in Windows Terminal {1}." -f `
                        $candidate.ProcessId, $terminalWindow.Id
                )
                return [ordered]@{
                    processId = [int]$candidate.ProcessId
                    terminalProcessId = [int]$terminalWindow.Id
                }
            }
        }
        Start-Sleep -Milliseconds 250
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw "Windows Terminal did not expose the visible Gateway host window"
}

function Test-GatewayReadyEndpoint {
    try {
        $status = Invoke-RestMethod `
            -Uri "http://127.0.0.1:$GatewayPort/readyz" `
            -TimeoutSec 5 `
            -Method Get
        return [bool]$status.ready -and @($status.failing).Count -eq 0
    }
    catch {
        return $false
    }
}

function Wait-GatewayReady {
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($ReadyTimeoutSeconds)
    do {
        Update-LifecycleLease -Phase "waiting_for_readiness"
        $gatewayProcesses = @(Get-GatewayProcesses)
        $listeners = @(Get-GatewayListener)
        if ($listeners.Count -eq 1 -and $gatewayProcesses.Count -eq 1 -and
            [int]$listeners[0].OwningProcess -eq [int]$gatewayProcesses[0].ProcessId) {
            if (Test-GatewayReadyEndpoint) {
                return [ordered]@{
                    processId = [int]$gatewayProcesses[0].ProcessId
                    listenerAddress = [string]$listeners[0].LocalAddress
                    readyEndpoint = "ok"
                }
            }
        }
        if ($gatewayProcesses.Count -gt 1 -or $listeners.Count -gt 1) {
            throw "OpenClaw Gateway started with duplicate processes or listeners"
        }
        Start-Sleep -Seconds 1
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw "Timed out waiting for the visible OpenClaw Gateway ready endpoint"
}

$mutex = [Threading.Mutex]::new(
    $false,
    "Local\AgentBridgeOpenClawLifecycle-$env:USERNAME"
)
$acquired = $false
try {
    $acquired = $mutex.WaitOne([TimeSpan]::FromSeconds(45))
    if (-not $acquired) {
        throw "Another AgentBridge OpenClaw lifecycle operation is already running"
    }

    $action = if ($StopOnly) { "stop" } elseif ($StartOnly) { "start" } else { "restart" }
    $script:operationAction = $action
    $script:operationInitialized = $true
    Update-LifecycleLease -Phase "lifecycle_acquired"
    $stoppedPids = @()
    $removedLocks = @()
    $foregroundHost = $null
    $existingProcesses = @(Get-GatewayProcesses)
    $existingListeners = @(Get-GatewayListener)

    $ready = $null
    if ($StartOnly -and $existingProcesses.Count -eq 1 -and
        $existingListeners.Count -eq 1 -and
        [int]$existingProcesses[0].ProcessId -eq [int]$existingListeners[0].OwningProcess -and
        (Test-GatewayReadyEndpoint) -and
        ($foregroundHost = Get-VisibleGatewayForeground `
            -GatewayProcessId ([int]$existingProcesses[0].ProcessId))) {
        $ready = Wait-GatewayReady
        $action = "already_running"
    }
    else {
        if (-not $StartOnly -or $existingProcesses.Count -gt 0 -or $existingListeners.Count -gt 0) {
            Update-LifecycleLease -Phase "stopping_previous_gateway"
            $stoppedPids = @(Stop-GatewayProcesses)
            Wait-GatewayStopped
        }
        Update-LifecycleLease -Phase "removing_stale_locks"
        $removedLocks = @(Remove-StaleGatewayLocks)
        if (-not $StopOnly) {
            Update-LifecycleLease -Phase "starting_gateway"
            $foregroundHost = Start-VisibleGateway
            $ready = Wait-GatewayReady
        }
        else {
            $ready = $null
        }
    }

    $visibleForeground = $false
    if (-not $StopOnly -and $ready) {
        Update-LifecycleLease -Phase "verifying_foreground"
        $actualForeground = Get-VisibleGatewayForeground `
            -GatewayProcessId ([int]$ready.processId)
        if (-not $actualForeground) {
            throw "OpenClaw Gateway is ready but is not hosted by a visible foreground window"
        }
        $foregroundHost = $actualForeground
        $visibleForeground = $true
    }

    $status = [ordered]@{
        schemaVersion = "agentbridge.openclaw-lifecycle.v1"
        status = "succeeded"
        action = $action
        observedAt = [DateTimeOffset]::UtcNow.ToString("o")
        gatewayPort = $GatewayPort
        gatewayProcessId = if ($ready) { $ready.processId } else { $null }
        listenerAddress = if ($ready) { $ready.listenerAddress } else { $null }
        readyEndpoint = if ($ready) { $ready.readyEndpoint } else { $null }
        terminalProcessId = if ($foregroundHost) {
            $foregroundHost.terminalProcessId
        } else {
            $null
        }
        visibleForeground = $visibleForeground
        stoppedProcessIds = $stoppedPids
        staleLocksRemoved = $removedLocks
        businessCalls = 0
        businessListReads = 0
        businessWrites = 0
        lifecycleOperationId = $script:operationId
    }
    [IO.File]::WriteAllText(
        $statusPath,
        ($status | ConvertTo-Json -Compress -Depth 5),
        [Text.UTF8Encoding]::new($false)
    )
    Write-LifecycleLog (
        "Gateway lifecycle {0} succeeded; pid={1}; staleLocks={2}." -f `
            $action, $status.gatewayProcessId, $removedLocks.Count
    )
    $script:operationAction = $action
    Update-LifecycleLease -State "completed" -Phase "completed"
    $script:operationFinished = $true
    $status | ConvertTo-Json -Compress -Depth 5
}
catch {
    if ($script:operationInitialized -and -not $script:operationFinished) {
        Update-LifecycleLease `
            -State "failed" `
            -Phase "failed" `
            -ErrorCode $_.Exception.GetType().Name
    }
    throw
}
finally {
    if ($acquired) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
