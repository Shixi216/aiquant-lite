param(
    [string]$ProjectRoot = "E:\hermes-opc"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
$env:UV_CACHE_DIR = Join-Path $ProjectRoot ".uv-cache"
$runtimeDirectory = Join-Path $ProjectRoot "runtime"
$logDirectory = Join-Path $ProjectRoot "logs\hermes-opc"
$stateFile = Join-Path $runtimeDirectory "hermes_opc_state.json"
$dataLauncherPidFile = Join-Path $runtimeDirectory "data_hub.launcher.pid"
$routerLauncherPidFile = Join-Path $runtimeDirectory "router.launcher.pid"
$dataStdout = Join-Path $logDirectory "data_hub.stdout.log"
$dataStderr = Join-Path $logDirectory "data_hub.stderr.log"
$routerStdout = Join-Path $logDirectory "router.stdout.log"
$routerStderr = Join-Path $logDirectory "router.stderr.log"

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

function Wait-HermesHealth {
    param(
        [string]$Url,
        [string]$ExpectedService,
        [string]$ExpectedVersion,
        [int]$Attempts = 30
    )

    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        try {
            $health = Invoke-RestMethod -Uri $Url -TimeoutSec 2
            if (
                $health.status -eq "ok" -and
                $health.service -eq $ExpectedService -and
                $health.version -eq $ExpectedVersion
            ) {
                return $health
            }
        }
        catch {
            # The process may still be importing modules.
        }
        Start-Sleep -Seconds 1
    }

    throw (
        "$ExpectedService did not become healthy at $Url " +
        "with version $ExpectedVersion."
    )
}

function Get-ProcessStartTime {
    param([int]$ProcessId)

    $process = Get-Process -Id $ProcessId -ErrorAction Stop
    return $process.StartTime.ToUniversalTime().ToString("o")
}

function Stop-StartedProcess {
    param([int]$ProcessId)

    if ($ProcessId -le 0) {
        return
    }

    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($null -ne $process) {
        Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
    }
}

if (-not (Test-Path -LiteralPath $ProjectRoot -PathType Container)) {
    throw "Project root does not exist: $ProjectRoot"
}

Set-Location -LiteralPath $ProjectRoot

$uvCommand = Get-Command uv -ErrorAction SilentlyContinue
if ($null -eq $uvCommand) {
    throw "uv was not found on PATH. Install or expose uv before starting Hermes-OPC."
}
$uvPath = if ($uvCommand.Source) { $uvCommand.Source } else { $uvCommand.Path }

New-Item -ItemType Directory -Force -Path $runtimeDirectory, $logDirectory |
Out-Null

if (Test-Path -LiteralPath $stateFile) {
    throw (
        "Managed state already exists: $stateFile. " +
        "Run scripts\status_hermes_opc.ps1 or scripts\stop_hermes_opc.ps1 first."
    )
}

foreach ($port in 8766, 8765) {
    $listener = Get-PortListener -Port $port
    if ($null -ne $listener) {
        throw "Port $port is already occupied by PID $($listener.OwningProcess)."
    }
}

Write-Host "Running Hermes-OPC preflight checks..."
$previousMaintenance = $env:HERMES_DB_MAINTENANCE
try {
    $env:HERMES_DB_MAINTENANCE = "1"
    & $uvPath run python -m scripts.preflight_check
    if ($LASTEXITCODE -ne 0) {
        throw "Hermes-OPC preflight checks blocked startup."
    }
}
finally {
    if ($null -eq $previousMaintenance) {
        Remove-Item Env:HERMES_DB_MAINTENANCE -ErrorAction SilentlyContinue
    }
    else {
        $env:HERMES_DB_MAINTENANCE = $previousMaintenance
    }
}

$versionOutput = & $uvPath run python -c (
    "from config.version import PROJECT_VERSION; print(PROJECT_VERSION)"
)
if ($LASTEXITCODE -ne 0 -or -not $versionOutput) {
    throw "Unable to read Hermes-OPC version from pyproject.toml."
}
$projectVersion = [string](@($versionOutput)[-1])
$projectVersion = $projectVersion.Trim()

$dataLauncherPid = 0
$dataServicePid = 0
$routerLauncherPid = 0
$routerServicePid = 0

try {
    Write-Host "Starting Router $projectVersion..."
    & $uvPath run python -m scripts.detached_service_launcher `
        --module "scripts.run_router_api" `
        --cwd $ProjectRoot `
        --stdout $routerStdout `
        --stderr $routerStderr `
        --pid-file $routerLauncherPidFile
    if ($LASTEXITCODE -ne 0) {
        throw "Router detached launcher failed."
    }

    $routerLauncherPid = [int](
        Get-Content -LiteralPath $routerLauncherPidFile -Raw
    ).Trim()
    $routerHealth = Wait-HermesHealth `
        -Url "http://127.0.0.1:8765/health" `
        -ExpectedService "Hermes OPC Agent Router" `
        -ExpectedVersion $projectVersion
    $routerListener = Get-PortListener -Port 8765
    if ($null -eq $routerListener) {
        throw "Router is healthy but its port listener was not found."
    }
    $routerServicePid = [int]$routerListener.OwningProcess

    Write-Host "Starting Data Hub compatibility proxy $projectVersion..."
    & $uvPath run python -m scripts.detached_service_launcher `
        --module "scripts.run_data_api" `
        --cwd $ProjectRoot `
        --stdout $dataStdout `
        --stderr $dataStderr `
        --pid-file $dataLauncherPidFile
    if ($LASTEXITCODE -ne 0) {
        throw "Data Hub compatibility proxy launcher failed."
    }

    $dataLauncherPid = [int](
        Get-Content -LiteralPath $dataLauncherPidFile -Raw
    ).Trim()
    $dataHealth = Wait-HermesHealth `
        -Url "http://127.0.0.1:8766/health" `
        -ExpectedService "Hermes OPC Data Hub" `
        -ExpectedVersion $projectVersion
    $dataListener = Get-PortListener -Port 8766
    if ($null -eq $dataListener) {
        throw "Data Hub is healthy but its port listener was not found."
    }
    $dataServicePid = [int]$dataListener.OwningProcess

    $state = [ordered]@{
        version = $projectVersion
        project_root = $ProjectRoot
        started_at = (Get-Date).ToUniversalTime().ToString("o")
        services = [ordered]@{
            data_hub = [ordered]@{
                pid = $dataServicePid
                pid_start_time = Get-ProcessStartTime -ProcessId $dataServicePid
                launcher_pid = $dataLauncherPid
                launcher_pid_start_time = Get-ProcessStartTime -ProcessId $dataLauncherPid
                port = 8766
                health_url = "http://127.0.0.1:8766/health"
                stdout_log = $dataStdout
                stderr_log = $dataStderr
            }
            router = [ordered]@{
                pid = $routerServicePid
                pid_start_time = Get-ProcessStartTime -ProcessId $routerServicePid
                launcher_pid = $routerLauncherPid
                launcher_pid_start_time = Get-ProcessStartTime -ProcessId $routerLauncherPid
                port = 8765
                health_url = "http://127.0.0.1:8765/health"
                stdout_log = $routerStdout
                stderr_log = $routerStderr
            }
        }
    }
    $state |
    ConvertTo-Json -Depth 6 |
    Set-Content -LiteralPath $stateFile -Encoding UTF8

    Write-Host ""
    Write-Host "Hermes-OPC $projectVersion started successfully."
    Write-Host "Data Hub PID: $dataServicePid"
    Write-Host "Data Hub log: $dataStdout"
    Write-Host "Data Hub API: http://127.0.0.1:8766"
    Write-Host "Data Hub docs: http://127.0.0.1:8766/docs"
    Write-Host "Router PID: $routerServicePid"
    Write-Host "Router log: $routerStdout"
    Write-Host "Router API: http://127.0.0.1:8765"
    Write-Host "Router docs: http://127.0.0.1:8765/docs"
    Write-Host "Finance MCP: stdio/on-demand (managed by Hermes)"
    Write-Host "Registered roles: $($routerHealth.registered_roles)"
    Write-Host "Enabled roles: $($routerHealth.enabled_roles)"
}
catch {
    $failure = $_
    [Console]::Error.WriteLine(
        "Hermes-OPC startup failed: $($failure.Exception.Message)"
    )
    Stop-StartedProcess -ProcessId $routerServicePid
    Stop-StartedProcess -ProcessId $routerLauncherPid
    Stop-StartedProcess -ProcessId $dataServicePid
    Stop-StartedProcess -ProcessId $dataLauncherPid
    Remove-Item -LiteralPath $stateFile -Force -ErrorAction SilentlyContinue
    throw $failure
}
