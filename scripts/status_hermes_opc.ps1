param(
    [string]$ProjectRoot = "E:\hermes-opc"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
$env:UV_CACHE_DIR = Join-Path $ProjectRoot ".uv-cache"
$stateFile = Join-Path $ProjectRoot "runtime\hermes_opc_state.json"

function Get-PortListener {
    param([int]$Port)

    $listener = Get-NetTCPConnection `
        -LocalAddress "127.0.0.1" `
        -LocalPort $Port `
        -State Listen `
        -ErrorAction SilentlyContinue |
    Select-Object -First 1
    if ($null -ne $listener) {
        return $listener
    }

    $netstat = Join-Path $env:SystemRoot "System32\netstat.exe"
    foreach ($line in (& $netstat -ano -p tcp)) {
        if ($line -match "^\s*TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+(\d+)\s*$") {
            return [pscustomobject]@{
                LocalAddress = "127.0.0.1"
                LocalPort = $Port
                OwningProcess = [int]$matches[1]
            }
        }
    }

    return $null
}

function Get-Health {
    param([string]$Url)

    try {
        return Invoke-RestMethod -Uri $Url -TimeoutSec 3
    }
    catch {
        return $null
    }
}

function Write-ServiceStatus {
    param(
        [string]$Name,
        [int]$Port,
        [string]$HealthUrl,
        [object]$RecordedService
    )

    $listener = Get-PortListener -Port $Port
    $health = Get-Health -Url $HealthUrl
    $recordedPid = if ($null -ne $RecordedService) {
        [int]$RecordedService.pid
    }
    else {
        $null
    }

    Write-Host "$Name"
    Write-Host "  Running: $($null -ne $listener)"
    Write-Host "  PID: $(if ($listener) { $listener.OwningProcess } else { 'none' })"
    Write-Host "  Managed PID: $(if ($recordedPid) { $recordedPid } else { 'none' })"
    Write-Host "  Port: $Port"
    Write-Host "  Health: $(if ($health) { $health.status } else { 'unavailable' })"
    Write-Host "  Version: $(if ($health) { $health.version } else { 'unavailable' })"
}

$state = if (Test-Path -LiteralPath $stateFile -PathType Leaf) {
    Get-Content -LiteralPath $stateFile -Raw |
    ConvertFrom-Json
}
else {
    $null
}

$versionMatch = Select-String `
    -LiteralPath (Join-Path $ProjectRoot "pyproject.toml") `
    -Pattern '^version\s*=\s*"([^"]+)"\s*$' |
Select-Object -First 1
$projectVersion = if ($versionMatch) {
    $versionMatch.Matches[0].Groups[1].Value
}
else {
    "unavailable"
}

Write-Host "Hermes-OPC local status"
Write-Host "Project root: $ProjectRoot"
Write-Host "Project version: $projectVersion"
Write-Host "Managed state: $(if ($state) { $stateFile } else { 'none' })"

Write-ServiceStatus `
    -Name "Data Hub" `
    -Port 8766 `
    -HealthUrl "http://127.0.0.1:8766/health" `
    -RecordedService $(if ($state) { $state.services.data_hub } else { $null })

Write-ServiceStatus `
    -Name "Router" `
    -Port 8765 `
    -HealthUrl "http://127.0.0.1:8765/health" `
    -RecordedService $(if ($state) { $state.services.router } else { $null })

$roles = try {
    Invoke-RestMethod -Uri "http://127.0.0.1:8765/v1/roles" -TimeoutSec 3
}
catch {
    $null
}

if ($roles) {
    $enabledRoles = @(
        $roles.roles |
        Where-Object { $_.enabled } |
        ForEach-Object { $_.role }
    )
    Write-Host "Registered roles: $($roles.count)"
    Write-Host "Enabled roles: $($roles.enabled_count)"
    Write-Host (
        "Enabled model role names: " +
        $(if ($enabledRoles.Count) { $enabledRoles -join ", " } else { "none" })
    )
}
else {
    Write-Host "Registered roles: unavailable"
    Write-Host "Enabled roles: unavailable"
    Write-Host "Enabled model role names: unavailable"
}

$projectPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $projectPython -PathType Leaf) {
    $mcpToolCount = & $projectPython -c (
        "from mcp_servers.finance_data.server import mcp; " +
        "print(len(mcp._tool_manager.list_tools()))"
    )
    if ($LASTEXITCODE -eq 0 -and $mcpToolCount) {
        Write-Host "Finance MCP: available (stdio/on-demand)"
        Write-Host "Finance MCP tools: $(@($mcpToolCount)[-1])"
    }
    else {
        Write-Host "Finance MCP: unavailable"
    }
}
else {
    Write-Host "Finance MCP: unavailable (project Python missing)"
}
