$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent (
    Split-Path -Parent $PSScriptRoot
)

$pidFile = Join-Path $projectRoot "logs\data_api.pid"
$healthUrl = "http://127.0.0.1:8766/health"
$port = 8766

$listener = Get-NetTCPConnection `
    -LocalPort $port `
    -State Listen `
    -ErrorAction SilentlyContinue |
Select-Object -First 1

if ($null -eq $listener) {
    Remove-Item `
        $pidFile `
        -Force `
        -ErrorAction SilentlyContinue

    Write-Host "Data Hub is not running."
    exit 0
}

try {
    $health = Invoke-RestMethod `
        -Uri $healthUrl `
        -TimeoutSec 3
}
catch {
    throw (
        "Port 8766 is listening, but Data Hub identity " +
        "could not be verified. PID: " +
        $listener.OwningProcess
    )
}

if ($health.service -ne "Hermes OPC Data Hub") {
    throw (
        "Port 8766 belongs to an unknown service. PID: " +
        $listener.OwningProcess
    )
}

$processId = [int]$listener.OwningProcess

Write-Host "Stopping Data Hub. PID: $processId"

Stop-Process `
    -Id $processId `
    -Force

for ($attempt = 1; $attempt -le 10; $attempt++) {
    Start-Sleep -Milliseconds 500

    $process = Get-Process `
        -Id $processId `
        -ErrorAction SilentlyContinue

    if ($null -eq $process) {
        break
    }
}

$remaining = Get-Process `
    -Id $processId `
    -ErrorAction SilentlyContinue

if ($null -ne $remaining) {
    throw "Data Hub process could not be stopped."
}

Remove-Item `
    $pidFile `
    -Force `
    -ErrorAction SilentlyContinue

Write-Host "Data Hub stopped."