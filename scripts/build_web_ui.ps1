param([switch]$InstallDependencies)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$localNode = Join-Path $projectRoot ".build-tools\node"
$previousPath = $env:PATH
try {
    if (Test-Path -LiteralPath (Join-Path $localNode "node.exe")) {
        $env:PATH = "$localNode;$env:PATH"
    }
    $npmCommand = Get-Command npm.cmd -ErrorAction Stop
    $frontendRoot = Join-Path $projectRoot "frontend"
    if ($InstallDependencies -or -not (Test-Path -LiteralPath (Join-Path $frontendRoot "node_modules"))) {
        & $npmCommand.Source --prefix $frontendRoot ci
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    & $npmCommand.Source --prefix $frontendRoot run build
    exit $LASTEXITCODE
}
finally {
    $env:PATH = $previousPath
}
