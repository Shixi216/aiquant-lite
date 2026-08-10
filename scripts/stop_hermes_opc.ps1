param(
    [string]$ProjectRoot = "E:\hermes-opc"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
$stateFile = Join-Path $ProjectRoot "runtime\hermes_opc_state.json"

function Test-RecordedProcess {
    param(
        [int]$ProcessId,
        [string]$RecordedStartTime
    )

    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        return $false
    }

    $actual = $process.StartTime.ToUniversalTime()
    $recorded = [DateTime]::Parse(
        $RecordedStartTime,
        [System.Globalization.CultureInfo]::InvariantCulture,
        [System.Globalization.DateTimeStyles]::RoundtripKind
    ).ToUniversalTime()
    return [Math]::Abs(($actual - $recorded).TotalSeconds) -lt 1
}

function Stop-RecordedProcess {
    param(
        [string]$Label,
        [int]$ProcessId,
        [string]$RecordedStartTime
    )

    if (-not (Test-RecordedProcess `
        -ProcessId $ProcessId `
        -RecordedStartTime $RecordedStartTime)) {
        $existing = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
        if ($null -eq $existing) {
            Write-Host "$Label is already stopped. Recorded PID: $ProcessId"
            return
        }
        throw (
            "$Label PID $ProcessId no longer matches its recorded start time. " +
            "It will not be stopped."
        )
    }

    Write-Host "Stopping $Label. PID: $ProcessId"
    Stop-Process -Id $ProcessId -Force
    for ($attempt = 1; $attempt -le 10; $attempt++) {
        if ($null -eq (
            Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
        )) {
            return
        }
        Start-Sleep -Milliseconds 500
    }
    throw "$Label PID $ProcessId did not stop."
}

if (-not (Test-Path -LiteralPath $stateFile -PathType Leaf)) {
    Write-Host (
        "No Hermes-OPC managed state was found. " +
        "No process has been stopped."
    )
    exit 0
}

$state = Get-Content -LiteralPath $stateFile -Raw |
ConvertFrom-Json

try {
    foreach ($serviceName in "router", "data_hub") {
        $service = $state.services.$serviceName
        $label = if ($serviceName -eq "router") { "Router" } else { "Data Hub" }

        Stop-RecordedProcess `
            -Label $label `
            -ProcessId ([int]$service.pid) `
            -RecordedStartTime ([string]$service.pid_start_time)

        if ([int]$service.launcher_pid -ne [int]$service.pid) {
            Stop-RecordedProcess `
                -Label "$label launcher" `
                -ProcessId ([int]$service.launcher_pid) `
                -RecordedStartTime ([string]$service.launcher_pid_start_time)
        }
    }
}
catch {
    throw
}

Remove-Item -LiteralPath $stateFile -Force
Remove-Item `
    -LiteralPath (
        Join-Path $ProjectRoot "runtime\data_hub.launcher.pid"
    ), (
        Join-Path $ProjectRoot "runtime\router.launcher.pid"
    ) `
    -Force `
    -ErrorAction SilentlyContinue

Write-Host "Hermes-OPC managed services stopped."
Write-Host "Finance MCP uses stdio/on-demand and has no managed background process."
