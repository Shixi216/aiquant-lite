$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent (
    Split-Path -Parent $PSScriptRoot
)

$pidFile = Join-Path $projectRoot "logs\router_api.pid"
$rootUrl = "http://127.0.0.1:8765/"
$port = 8765

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

    Write-Host "Agent Router is not running."
    exit 0
}

try {
    $root = Invoke-RestMethod `
        -Uri $rootUrl `
        -TimeoutSec 3
}
catch {
    throw (
        "Port 8765 is listening, but service identity " +
        "could not be verified. PID: " +
        $listener.OwningProcess
    )
}

if ($root.service -ne "Hermes OPC Agent Router") {
    throw (
        "Port 8765 belongs to an unknown service. PID: " +
        $listener.OwningProcess
    )
}

$processId = [int]$listener.OwningProcess

Write-Host "Stopping Agent Router. PID: $processId"

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
    throw "Agent Router process could not be stopped."
}

Remove-Item `
    $pidFile `
    -Force `
    -ErrorAction SilentlyContinue

Write-Host "Agent Router stopped."