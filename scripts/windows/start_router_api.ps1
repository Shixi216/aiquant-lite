$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent (
    Split-Path -Parent $PSScriptRoot
)

$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$launcher = Join-Path $projectRoot "scripts\detached_service_launcher.py"
$pidFile = Join-Path $projectRoot "logs\router_api.pid"
$stdoutFile = Join-Path $projectRoot "logs\router_api.stdout.log"
$stderrFile = Join-Path $projectRoot "logs\router_api.stderr.log"
$rootUrl = "http://127.0.0.1:8765/"
$healthUrl = "http://127.0.0.1:8765/health"
$port = 8765

function Test-RouterIdentity {
    try {
        $root = Invoke-RestMethod `
            -Uri $rootUrl `
            -TimeoutSec 3

        return (
            $root.service -eq "Hermes OPC Agent Router"
        )
    }
    catch {
        return $false
    }
}

function Get-RouterListener {
    return Get-NetTCPConnection `
        -LocalPort $port `
        -State Listen `
        -ErrorAction SilentlyContinue |
    Select-Object -First 1
}

if (-not (Test-Path $python)) {
    throw "Python not found: $python"
}

if (-not (Test-Path $launcher)) {
    throw "Detached launcher not found: $launcher"
}

New-Item `
    -ItemType Directory `
    -Force `
    (Join-Path $projectRoot "logs") |
Out-Null

$listener = Get-RouterListener

if ($null -ne $listener) {
    if (-not (Test-RouterIdentity)) {
        throw (
            "Port 8765 is occupied by another process. PID: " +
            $listener.OwningProcess
        )
    }

    $servicePid = [int]$listener.OwningProcess

    [System.IO.File]::WriteAllText(
        $pidFile,
        [string]$servicePid,
        [System.Text.Encoding]::ASCII
    )

    Write-Host "Agent Router is already running. PID: $servicePid"
    Write-Host "Service URL: http://127.0.0.1:8765"
    exit 0
}

Remove-Item `
    $pidFile, $stdoutFile, $stderrFile `
    -Force `
    -ErrorAction SilentlyContinue

& $python `
    $launcher `
    --module "scripts.run_router_api" `
    --cwd $projectRoot `
    --stdout $stdoutFile `
    --stderr $stderrFile `
    --pid-file $pidFile

if ($LASTEXITCODE -ne 0) {
    throw "Detached process launcher failed."
}

$started = $false

for ($attempt = 1; $attempt -le 20; $attempt++) {
    Start-Sleep -Seconds 1

    if (Test-RouterIdentity) {
        $started = $true
        break
    }
}

if (-not $started) {
    Write-Host "Agent Router failed to start."

    if (Test-Path $stderrFile) {
        Write-Host "Recent error log:"
        Get-Content $stderrFile -Tail 50
    }

    Remove-Item `
        $pidFile `
        -Force `
        -ErrorAction SilentlyContinue

    throw "Agent Router identity check failed."
}

$serviceListener = Get-RouterListener

if ($null -eq $serviceListener) {
    throw "Agent Router listener was not found."
}

$servicePid = [int]$serviceListener.OwningProcess

[System.IO.File]::WriteAllText(
    $pidFile,
    [string]$servicePid,
    [System.Text.Encoding]::ASCII
)

Write-Host "Agent Router started. PID: $servicePid"
Write-Host "Service URL: http://127.0.0.1:8765"
Write-Host "API docs: http://127.0.0.1:8765/docs"

try {
    $health = Invoke-RestMethod `
        -Uri $healthUrl `
        -TimeoutSec 3

    Write-Host "Health status: $($health.status)"
    Write-Host "Data Hub status: $($health.data_hub.status)"
}
catch {
    Write-Host "Health status: degraded or unavailable"
}