param(
    [string]$ProjectRoot = "E:\hermes-opc"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$resolvedRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
$python = Join-Path $resolvedRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Project Python was not found."
}

Set-Location -LiteralPath $resolvedRoot
& $python -m scripts.system_cli health
exit $LASTEXITCODE
