param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
)

$ErrorActionPreference = "Stop"
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$sourceRelative = "trading\research\sentiment\v3"
$sourcePath = Join-Path $ProjectRoot $sourceRelative
$backupRoot = Join-Path $ProjectRoot "recovery"
$backupPath = Join-Path $backupRoot "sentiment_v3_corrupt_$timestamp"
$manifestPath = Join-Path $ProjectRoot "Sentiment_V3_Recovery_Manifest.json"

if (-not (Test-Path -LiteralPath $sourcePath -PathType Container)) {
    throw "Sentiment V3 source directory not found: $sourcePath"
}
if (Test-Path -LiteralPath $backupPath) {
    throw "Backup path already exists: $backupPath"
}

$files = @(
    Get-ChildItem -LiteralPath $sourcePath -File -Recurse -Force |
        Sort-Object FullName |
        ForEach-Object {
            [ordered]@{
                path = [System.IO.Path]::GetRelativePath($ProjectRoot, $_.FullName).Replace("\", "/")
                size_bytes = $_.Length
                mtime = $_.LastWriteTimeUtc.ToString("o")
                sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            }
        }
)

$gitStatus = @(& git -C $ProjectRoot status --porcelain=v1 --untracked-files=all 2>&1)
$untracked = @(& git -C $ProjectRoot ls-files --others --exclude-standard 2>&1)
$branch = (& git -C $ProjectRoot branch --show-current 2>&1 | Out-String).Trim()
$head = (& git -C $ProjectRoot rev-parse HEAD 2>&1 | Out-String).Trim()

$testFiles = @(
    Get-ChildItem -LiteralPath (Join-Path $ProjectRoot "tests") -File -Filter "test_sentiment_v3_*.py" |
        Sort-Object Name |
        ForEach-Object { $_.FullName }
)
$testOutput = @()
$testExitCode = $null
if ($testFiles.Count -gt 0) {
    Push-Location $ProjectRoot
    try {
        $testOutput = @(& uv run pytest -q @testFiles 2>&1)
        $testExitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
}

New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
Copy-Item -LiteralPath $sourcePath -Destination $backupPath -Recurse -Force

$manifest = [ordered]@{
    schema_version = 1
    created_at = (Get-Date).ToUniversalTime().ToString("o")
    project_root = $ProjectRoot
    source_path = $sourceRelative.Replace("\", "/")
    backup_path = [System.IO.Path]::GetRelativePath($ProjectRoot, $backupPath).Replace("\", "/")
    git = [ordered]@{
        branch = $branch
        head = $head
        status_porcelain = $gitStatus
        untracked_files = $untracked
    }
    current_test_state = [ordered]@{
        command = "uv run pytest -q tests/test_sentiment_v3_*.py"
        exit_code = $testExitCode
        output = $testOutput
    }
    source_inventory = $files
    recovery_status = @{}
}

$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath -Encoding utf8
Write-Output "manifest=$manifestPath"
Write-Output "backup=$backupPath"
Write-Output "files=$($files.Count)"
Write-Output "test_exit_code=$testExitCode"
