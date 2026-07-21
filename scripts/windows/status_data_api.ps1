$projectRoot = Split-Path -Parent (
    Split-Path -Parent $PSScriptRoot
)

$pidFile = Join-Path $projectRoot "logs\data_api.pid"
$healthUrl = "http://127.0.0.1:8766/health"
$port = 8766

Write-Host "Data Hub status"
Write-Host "PID file: $pidFile"

if (Test-Path $pidFile) {
    $storedPid = (Get-Content $pidFile -Raw).Trim()
    Write-Host "Stored PID: $storedPid"
}
else {
    Write-Host "Stored PID: none"
}

$listener = Get-NetTCPConnection `
    -LocalPort $port `
    -State Listen `
    -ErrorAction SilentlyContinue |
Select-Object -First 1

if ($null -ne $listener) {
    Write-Host "Port 8766: listening"
    Write-Host "Listener PID: $($listener.OwningProcess)"
}
else {
    Write-Host "Port 8766: not listening"
}

try {
    $health = Invoke-RestMethod `
        -Uri $healthUrl `
        -TimeoutSec 3

    Write-Host "Health status: $($health.status)"
    Write-Host "Service: $($health.service)"
    Write-Host "Database status: $($health.database.status)"
    Write-Host "Service URL: http://127.0.0.1:8766"
}
catch {
    Write-Host "Health status: unavailable"
}