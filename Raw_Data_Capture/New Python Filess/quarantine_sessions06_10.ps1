[CmdletBinding()]
param(
    [switch]$Execute,
    [string]$RawProjectRoot,
    [string]$ModelzooRoot
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$targetSessions = 6..10 | ForEach-Object { "session{0:D2}" -f $_ }
$confirmationText = "QUARANTINE SESSION06-10"
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"

if ([string]::IsNullOrWhiteSpace($RawProjectRoot)) {
    $RawProjectRoot = $PSScriptRoot
}
if ([string]::IsNullOrWhiteSpace($RawProjectRoot)) {
    $RawProjectRoot = (Get-Location).Path
}

$RawProjectRoot = (Resolve-Path -LiteralPath $RawProjectRoot).Path

if (-not $ModelzooRoot) {
    $candidate = Join-Path $RawProjectRoot "..\..\modelzoo"
    if (-not (Test-Path -LiteralPath $candidate -PathType Container)) {
        throw "Could not locate modelzoo automatically. Pass -ModelzooRoot explicitly."
    }
    $ModelzooRoot = (Resolve-Path -LiteralPath $candidate).Path
}
else {
    $ModelzooRoot = (Resolve-Path -LiteralPath $ModelzooRoot).Path
}

$pilotRoot = Join-Path $RawProjectRoot "dataset\model-pilot"
$rawSubjectRoot = Join-Path $pilotRoot "raw\fs2000\subject01"
$manifestPath = Join-Path $pilotRoot "capture_manifest.jsonl"
$embedded10Sessions = Join-Path $pilotRoot "embedded-q15-offset512-10sessions"
$radarRoot = Join-Path $ModelzooRoot "data\radar"
$activeRadarDataset = Join-Path $radarRoot "offset512-far0"
$stagingRadarDataset = Join-Path $radarRoot "offset512-far0-10sessions-staging"
$outputsRoot = Join-Path $ModelzooRoot "outputs"
$quarantineRoot = Join-Path $RawProjectRoot "quarantine\invalid_iq_wiring_sessions06-10_$timestamp"

if (-not (Test-Path -LiteralPath $rawSubjectRoot -PathType Container)) {
    throw "Raw dataset root is missing: $rawSubjectRoot"
}
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Capture manifest is missing: $manifestPath"
}

# Read and validate the complete JSONL manifest before changing anything.
$manifestLines = @(Get-Content -LiteralPath $manifestPath | Where-Object { $_.Trim() })
$keptManifestLines = [System.Collections.Generic.List[string]]::new()
$removedManifestLines = [System.Collections.Generic.List[string]]::new()
$removedBySession = @{}

foreach ($session in $targetSessions) {
    $removedBySession[$session] = 0
}

foreach ($line in $manifestLines) {
    try {
        $record = $line | ConvertFrom-Json
    }
    catch {
        throw "Manifest contains invalid JSON. No changes were made. Problematic line: $line"
    }

    if ($record.session_id -in $targetSessions) {
        $removedManifestLines.Add($line)
        $removedBySession[[string]$record.session_id]++
    }
    else {
        $keptManifestLines.Add($line)
    }
}

$rawSessionTargets = foreach ($session in $targetSessions) {
    $path = Join-Path $rawSubjectRoot $session
    [PSCustomObject]@{
        Session  = $session
        Path     = $path
        Exists   = Test-Path -LiteralPath $path -PathType Container
        CSV      = @(Get-ChildItem -LiteralPath $path -Recurse -File -Filter "*.csv" -ErrorAction SilentlyContinue).Count
        Metadata = @(Get-ChildItem -LiteralPath $path -Recurse -File -Filter "*.metadata.json" -ErrorAction SilentlyContinue).Count
        Manifest = $removedBySession[$session]
    }
}

$tenSessionOutputTargets = @()
if (Test-Path -LiteralPath $outputsRoot -PathType Container) {
    $tenSessionOutputTargets = @(
        Get-ChildItem -LiteralPath $outputsRoot -Directory |
            Where-Object { $_.Name -like "*10sessions*" }
    )
}

Write-Host "`nRaw sessions that will leave the active dataset:" -ForegroundColor Cyan
$rawSessionTargets | Format-Table Session, Exists, CSV, Metadata, Manifest

Write-Host "Derived datasets that will be quarantined if present:" -ForegroundColor Cyan
@($embedded10Sessions, $activeRadarDataset, $stagingRadarDataset) |
    ForEach-Object {
        [PSCustomObject]@{ Exists = Test-Path -LiteralPath $_; Path = $_ }
    } |
    Format-Table -AutoSize

Write-Host "Ten-session training-output directories that will be quarantined:" -ForegroundColor Cyan
if ($tenSessionOutputTargets.Count -eq 0) {
    Write-Host "  None found."
}
else {
    $tenSessionOutputTargets | Select-Object FullName | Format-Table -AutoSize
}

Write-Host "Manifest rows to remove: $($removedManifestLines.Count)"
Write-Host "Manifest rows to keep:   $($keptManifestLines.Count)"
Write-Host "Quarantine destination:  $quarantineRoot"

if (-not $Execute) {
    Write-Host "`nPREVIEW ONLY - nothing was changed." -ForegroundColor Yellow
    Write-Host "Run again with -Execute when the targets above are correct."
    exit 0
}

$answer = Read-Host "Type '$confirmationText' to continue"
if ($answer -cne $confirmationText) {
    Write-Host "Confirmation did not match. Nothing was changed." -ForegroundColor Yellow
    exit 1
}

New-Item -ItemType Directory -Path $quarantineRoot -Force | Out-Null

function Move-ToQuarantine {
    param(
        [Parameter(Mandatory)][string]$Source,
        [Parameter(Mandatory)][string]$RelativeDestination
    )

    if (-not (Test-Path -LiteralPath $Source)) {
        return
    }

    $destination = Join-Path $quarantineRoot $RelativeDestination
    $destinationParent = Split-Path -Parent $destination
    New-Item -ItemType Directory -Path $destinationParent -Force | Out-Null
    Move-Item -LiteralPath $Source -Destination $destination
    Write-Host "Quarantined: $Source"
}

# Preserve the original manifest before replacing the active copy.
$manifestBackupDirectory = Join-Path $quarantineRoot "manifest_backup"
New-Item -ItemType Directory -Path $manifestBackupDirectory -Force | Out-Null
Copy-Item -LiteralPath $manifestPath -Destination (Join-Path $manifestBackupDirectory "capture_manifest_before_cleanup.jsonl")

# Move the invalid raw captures away so the collector can reuse session06-10.
foreach ($item in $rawSessionTargets) {
    Move-ToQuarantine -Source $item.Path -RelativeDestination ("raw\" + $item.Session)
}

# These products contain tensors or checkpoints derived from the invalid sessions.
Move-ToQuarantine -Source $embedded10Sessions -RelativeDestination "derived_raw\embedded-q15-offset512-10sessions"
Move-ToQuarantine -Source $activeRadarDataset -RelativeDestination "modelzoo_data\offset512-far0-invalid-10sessions"
Move-ToQuarantine -Source $stagingRadarDataset -RelativeDestination "modelzoo_data\offset512-far0-10sessions-staging"

foreach ($directory in $tenSessionOutputTargets) {
    Move-ToQuarantine -Source $directory.FullName -RelativeDestination ("modelzoo_outputs\" + $directory.Name)
}

# Rewrite the active manifest without session06-10. Use UTF-8 without a BOM.
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[System.IO.File]::WriteAllLines($manifestPath, $keptManifestLines, $utf8NoBom)
[System.IO.File]::WriteAllLines(
    (Join-Path $quarantineRoot "removed_manifest_rows_session06-10.jsonl"),
    $removedManifestLines,
    $utf8NoBom
)

# Post-change safety checks.
$remainingTargetRows = 0
foreach ($line in Get-Content -LiteralPath $manifestPath) {
    if (-not $line.Trim()) { continue }
    $record = $line | ConvertFrom-Json
    if ($record.session_id -in $targetSessions) {
        $remainingTargetRows++
    }
}

$remainingRawDirectories = @(
    $targetSessions | Where-Object {
        Test-Path -LiteralPath (Join-Path $rawSubjectRoot $_)
    }
)

if ($remainingTargetRows -ne 0 -or $remainingRawDirectories.Count -ne 0) {
    throw "Cleanup verification failed. Do not start recording until the active paths are inspected."
}

Write-Host "`nCleanup completed and verified." -ForegroundColor Green
Write-Host "Sessions06-10 are absent from the active raw tree and manifest."
Write-Host "Recoverable quarantine: $quarantineRoot"
Write-Host "The mixed ten-session modelzoo dataset is no longer active; regenerate it after recording."
