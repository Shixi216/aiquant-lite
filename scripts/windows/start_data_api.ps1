$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent (
    Split-Path -Parent $PSScriptRoot
)

$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$launcher = Join-Path $projectRoot "scripts\detached_service_launcher.py"
$pidFile = Join-Path $projectRoot "logs\data_api.pid"
$stdoutFile = Join-Path $projectRoot "logs\data_api.stdout.log"
$stderrFile = Join-Path $projectRoot "logs\data_api.stderr.log"
$rootUrl = "http://127.0.0.1:8766/"
$healthUrl = "http://127.0.0.1:8766/health"
$port = 8766

function Test-DataHubIdentity {
    try {
        $root = Invoke-RestMethod `
            -Uri $rootUrl `
            -TimeoutSec 3

        return (
            $root.service -eq "Hermes OPC Data Hub"
        )
    }
    catch {
        return $false
    }
}

function Test-DataHubHealth {
    try {
        $health = Invoke-RestMethod `
            -Uri $healthUrl `
            -TimeoutSec 3

        return (
            $health.status -eq "ok" -and
            $health.database.status -eq "ok"
        )
    }
    catch {
        return $false
    }
}

function Get-DataHubListener {
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

$listener = Get-DataHubListener

if ($null -ne $listener) {
    if (-not (Test-DataHubIdentity)) {
        throw (
            "Port 8766 is occupied by another process. PID: " +
            $listener.OwningProcess
        )
    }

    $servicePid = [int]$listener.OwningProcess

    [System.IO.File]::WriteAllText(
        $pidFile,
        [string]$servicePid,
        [System.Text.Encoding]::ASCII
    )

    Write-Host "Data Hub is already running. PID: $servicePid"
    Write-Host "Service URL: http://127.0.0.1:8766"
    exit 0
}

Remove-Item `
    $pidFile, $stdoutFile, $stderrFile `
    -Force `
    -ErrorAction SilentlyContinue

& $python `
    $launcher `
    --module "scripts.run_data_api" `
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

    if (Test-DataHubHealth) {
        $started = $true
        break
    }
}

if (-not $started) {
    Write-Host "Data Hub failed to start."

    if (Test-Path $stderrFile) {
        Write-Host "Recent error log:"
        Get-Content $stderrFile -Tail 50
    }

    Remove-Item `
        $pidFile `
        -Force `
        -ErrorAction SilentlyContinue

    throw "Data Hub health check failed."
}

$serviceListener = Get-DataHubListener

if ($null -eq $serviceListener) {
    throw "Data Hub listener was not found."
}

$servicePid = [int]$serviceListener.OwningProcess

[System.IO.File]::WriteAllText(
    $pidFile,
    [string]$servicePid,
    [System.Text.Encoding]::ASCII
)

Write-Host "Data Hub started. PID: $servicePid"
Write-Host "Service URL: http://127.0.0.1:8766"
Write-Host "API docs: http://127.0.0.1:8766/docs"