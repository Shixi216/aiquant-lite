param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$GitTree = "b3b279a9b4903632f62da431660775cc2d59c6aa",
    [string]$BaseTranscript = "C:\Users\userr\.gemini\antigravity\brain\b68fb6ef-e996-4c62-907b-4f6f24c69329\.system_generated\logs\transcript_full.jsonl",
    [string]$PatchTranscript = "C:\Users\userr\.gemini\antigravity\brain\e74a2970-022c-442d-ab03-a6a3c78d1685\.system_generated\logs\transcript_full.jsonl"
)

$ErrorActionPreference = "Stop"
$manifestPath = Join-Path $ProjectRoot "Sentiment_V3_Recovery_Manifest.json"
$v3Relative = "trading/research/sentiment/v3"
$v3Path = Join-Path $ProjectRoot $v3Relative
$recoveryRoot = Join-Path $ProjectRoot "recovery"
$stagePath = Join-Path $recoveryRoot "git_tree_$GitTree"
$archivePath = Join-Path $recoveryRoot "git_tree_$GitTree.tar"
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)

if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Recovery manifest is required before restoration: $manifestPath"
}
if (-not (Test-Path -LiteralPath $BaseTranscript -PathType Leaf)) {
    throw "Base transcript not found: $BaseTranscript"
}
if (-not (Test-Path -LiteralPath $PatchTranscript -PathType Leaf)) {
    throw "Patch transcript not found: $PatchTranscript"
}

New-Item -ItemType Directory -Path $recoveryRoot -Force | Out-Null
if (Test-Path -LiteralPath $stagePath) {
    throw "Recovery staging path already exists: $stagePath"
}
New-Item -ItemType Directory -Path $stagePath | Out-Null

& git -C $ProjectRoot archive --format=tar --output=$archivePath $GitTree $v3Relative
if ($LASTEXITCODE -ne 0) {
    throw "git archive failed for tree $GitTree"
}
& tar -xf $archivePath -C $stagePath
if ($LASTEXITCODE -ne 0) {
    throw "tar extraction failed for $archivePath"
}

$stagedV3 = Join-Path $stagePath "trading\research\sentiment\v3"
$gitFiles = @(Get-ChildItem -LiteralPath $stagedV3 -File -Filter "*.py")
foreach ($file in $gitFiles) {
    Copy-Item -LiteralPath $file.FullName -Destination (Join-Path $v3Path $file.Name) -Force
}

$baseLine = (Get-Content -LiteralPath $BaseTranscript -TotalCount 8)[7]
$baseEntry = $baseLine | ConvertFrom-Json -Depth 50
$historyFiles = @{}
foreach ($call in @($baseEntry.tool_calls)) {
    if ($call.name -ne "write_to_file") {
        continue
    }
    $target = [string]$call.args.TargetFile
    if ($target -notmatch "trading[\\/]research[\\/]sentiment[\\/]v3[\\/](live_shadow|shadow_drift_monitor)\.py$") {
        continue
    }
    $leaf = Split-Path -Leaf $target
    $destination = Join-Path $v3Path $leaf
    $content = ([string]$call.args.CodeContent).Replace("`r`n", "`n")
    [System.IO.File]::WriteAllText($destination, $content, $utf8NoBom)
    $historyFiles[$leaf] = "antigravity:$($baseEntry.step_index):$($call.name)"
}

$patchCount = 0
$lineNumber = 0
foreach ($line in Get-Content -LiteralPath $PatchTranscript) {
    $lineNumber++
    try {
        $entry = $line | ConvertFrom-Json -Depth 50
    }
    catch {
        continue
    }
    foreach ($call in @($entry.tool_calls)) {
        if ($call.name -ne "replace_file_content") {
            continue
        }
        $target = [string]$call.args.TargetFile
        if ($target -notmatch "trading[\\/]research[\\/]sentiment[\\/]v3[\\/][^\\/]+\.py$") {
            continue
        }
        $leaf = Split-Path -Leaf $target
        $destination = Join-Path $v3Path $leaf
        if (-not (Test-Path -LiteralPath $destination -PathType Leaf)) {
            throw "Patch target does not exist: $destination"
        }
        $content = [System.IO.File]::ReadAllText($destination).Replace("`r`n", "`n")
        $targetContent = ([string]$call.args.TargetContent).Replace("`r`n", "`n")
        $replacement = ([string]$call.args.ReplacementContent).Replace("`r`n", "`n")
        $first = $content.IndexOf($targetContent, [System.StringComparison]::Ordinal)
        $last = $content.LastIndexOf($targetContent, [System.StringComparison]::Ordinal)
        if ($first -lt 0) {
            throw "Historical patch target not found: $leaf transcript line $lineNumber"
        }
        if ($first -ne $last) {
            throw "Historical patch target is ambiguous: $leaf transcript line $lineNumber"
        }
        $updated = $content.Substring(0, $first) + $replacement + $content.Substring($first + $targetContent.Length)
        [System.IO.File]::WriteAllText($destination, $updated, $utf8NoBom)
        $patchCount++
    }
}

$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json -Depth 50
$status = [ordered]@{}
foreach ($file in Get-ChildItem -LiteralPath $v3Path -File -Filter "*.py" | Sort-Object Name) {
    $source = if ($historyFiles.ContainsKey($file.Name)) {
        $historyFiles[$file.Name]
    }
    else {
        "git-tree:$GitTree"
    }
    $status[$file.Name] = [ordered]@{
        status = "RECOVERED"
        source = $source
        size_bytes = $file.Length
        sha256 = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}
$manifest.recovery_status = $status
$manifest | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $manifestPath -Encoding utf8

Write-Output "git_files_recovered=$($gitFiles.Count)"
Write-Output "history_files_recovered=$($historyFiles.Count)"
Write-Output "historical_patches_applied=$patchCount"
Write-Output "zero_byte_py_files=$(@(Get-ChildItem -LiteralPath $v3Path -File -Filter '*.py' | Where-Object Length -eq 0).Count)"
