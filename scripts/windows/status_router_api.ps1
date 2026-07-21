$projectRoot = Split-Path -Parent (
    Split-Path -Parent $PSScriptRoot
)

$pidFile = Join-Path $projectRoot "logs\router_api.pid"
$rootUrl = "http://127.0.0.1:8765/"
$healthUrl = "http://127.0.0.1:8765/health"
$port = 8765

Write-Host "Agent Router status"
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
    Write-Host "Port 8765: listening"
    Write-Host "Listener PID: $($listener.OwningProcess)"
}
else {
    Write-Host "Port 8765: not listening"
}

try {
    $root = Invoke-RestMethod `
        -Uri $rootUrl `
        -TimeoutSec 3

    Write-Host "Service: $($root.service)"
}
catch {
    Write-Host "Service: unavailable"
}

try {
    $health = Invoke-RestMethod `
        -Uri $healthUrl `
        -TimeoutSec 3

    Write-Host "Health status: $($health.status)"
    Write-Host "Data Hub status: $($health.data_hub.status)"
    Write-Host "Registered roles: $($health.registered_roles)"
    Write-Host "Enabled roles: $($health.enabled_roles)"
    Write-Host "Service URL: http://127.0.0.1:8765"
}
catch {
    Write-Host "Health status: unavailable"
}