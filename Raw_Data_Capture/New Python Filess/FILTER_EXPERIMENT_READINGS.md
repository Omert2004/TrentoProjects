# Filter Experiment Readings and Reproduction Commands

This document records the first controlled 2 kHz raw-I/Q filter experiment and
the exact commands needed to reproduce its processing. It complements
`FILTER_EXPERIMENT_METHODS.md`, which describes the acquisition protocol.

## Scope

- Firmware stream: packetized, CRC-protected raw complex I/Q
- Sampling rate: 2000 samples/s
- STFT: 256 samples, 128-sample hop, Hann window
- Conditions: empty scene plus stationary, slow, normal, and fast hand cases
- Distances: near, mid, and far for every nonempty condition
- Total source captures: 13
- Subject: `subject01`

Run the commands below from the directory containing the Python tools, for
example:

```powershell
cd "C:\Users\Oguzm\OneDrive - ozyegin.edu.tr\Desktop\Github_Projects\TrentoProjects\Raw_Data_Capture\Python Files"
```

## Source-capture integrity

The metadata check reported:

```text
Metadata files: 13
Failed captures: 0
```

Inspection of the uploaded metadata also confirmed that all 13 files had:

- Host transport validation: `PASS`
- CRC errors: 0
- Packet-sequence gaps: 0
- Sample-index gaps: 0
- Reported MCU drop increase: 0
- Observed receive rate: approximately 1991.15 to 1992.21 samples/s

Re-run the metadata check with:

```powershell
$root = ".\dataset\filter-experiments"
$metadata = Get-ChildItem "$root\raw\fs2000" -Recurse -Filter *.metadata.json

"Metadata files: $($metadata.Count)"

$failed = $metadata | Where-Object {
  -not ((Get-Content $_.FullName -Raw | ConvertFrom-Json).host_transport_validation_passed)
}

"Failed captures: $($failed.Count)"
$failed.FullName
```

Do not use the experiment for filter selection unless the output remains 13
metadata files and zero failures.

## Unfiltered reference spectrograms

The baseline used no clutter filter:

```text
filter=none, FFT=256, hop=128, window=Hann
```

Generated column counts were:

| Condition | Distance | STFT columns |
|---|---:|---:|
| Empty scene | n/a | 310 |
| Stationary hand | near | 310 |
| Stationary hand | mid | 310 |
| Stationary hand | far | 310 |
| Slow movement | near | 466 |
| Slow movement | mid | 466 |
| Slow movement | far | 466 |
| Normal movement | near | 466 |
| Normal movement | mid | 466 |
| Normal movement | far | 466 |
| Fast movement | near | 466 |
| Fast movement | mid | 466 |
| Fast movement | far | 466 |

Generate the references while preserving the condition/distance hierarchy:

```powershell
$rawRoot = (Resolve-Path ".\dataset\filter-experiments\raw\fs2000").Path
$outRoot = ".\dataset\filter-experiments\spectrogram\fs2000\none"

Get-ChildItem $rawRoot -Recurse -Filter *.csv |
  Sort-Object FullName |
  ForEach-Object {
    $relativeDirectory = $_.DirectoryName.Substring($rawRoot.Length).TrimStart("\")
    $destination = Join-Path $outRoot $relativeDirectory
    New-Item -ItemType Directory -Force -Path $destination | Out-Null

    $outputFile = Join-Path $destination "$($_.BaseName)_spectrogram_none.png"

    python3.11 .\spectrogram_view.py $_.FullName `
      --filter none `
      --fft-size 256 `
      --hop 128 `
      --window hann `
      --out $outputFile `
      --force
  }
```

These PNGs are human-readable engineering references only. They contain plot
decorations and may use display scaling, so they are not model inputs.

## Candidate selected for the next evaluation stage

The first complete candidate evaluation used:

| Parameter | Value |
|---|---:|
| Clutter filter | Single-delay difference, `y[n] = x[n] - x[n-1]` |
| Q15 scaling | `DIFF_SHIFT=6` |
| FFT size | 256 |
| Hop | 128 |
| Window | Hann |
| DC guard | ±20 Hz |
| Detection threshold | 12 dB above the estimated noise floor |
| Clustering | 8-connected |
| Minimum component area | 8 pixels |

`DIFF_SHIFT` is the Q15 gain applied after differencing; it is not a separate
filter. Shift 6 was chosen for continued evaluation because the recorded data
showed a worst-case clipping rate of only 1/59,776 samples (0.0017%). Initial
screening found that shifts 7 and 8 clipped too much.

This is a characterization candidate, not yet a frozen production pipeline.
Final selection still requires model-window generation, fixed normalization,
on-chip parity, and inference testing.

## Candidate results

| Condition | Distance | Q15 clipping | Kept components | Removed components | Kept pixels |
|---|---:|---:|---:|---:|---:|
| Empty scene | n/a | 0/39,840 (0.0000%) | 0 | 89 | 0 |
| Stationary hand | near | 0/39,840 (0.0000%) | 8 | 92 | 861 |
| Stationary hand | mid | 0/39,840 (0.0000%) | 6 | 66 | 1,029 |
| Stationary hand | far | 0/39,840 (0.0000%) | 4 | 87 | 1,339 |
| Slow movement | near | 0/59,776 (0.0000%) | 40 | 135 | 1,506 |
| Slow movement | mid | 0/59,776 (0.0000%) | 14 | 123 | 1,178 |
| Slow movement | far | 0/59,776 (0.0000%) | 8 | 143 | 1,326 |
| Normal movement | near | 0/59,776 (0.0000%) | 101 | 225 | 4,256 |
| Normal movement | mid | 0/59,776 (0.0000%) | 12 | 199 | 1,054 |
| Normal movement | far | 1/59,776 (0.0017%) | 9 | 164 | 1,580 |
| Fast movement | near | 0/59,776 (0.0000%) | 245 | 643 | 5,918 |
| Fast movement | mid | 0/59,776 (0.0000%) | 48 | 340 | 1,551 |
| Fast movement | far | 0/59,776 (0.0000%) | 15 | 239 | 1,335 |

Important interpretation notes:

- The empty-scene result kept zero components, which is encouraging for false-positive suppression.
- Slow/far movement retained 1,326 pixels in 8 components, so the candidate did not erase the weakest tested movement case.
- Stationary-hand components mainly include placement/removal transients and small hand motion; they must not automatically be interpreted as a stationary classification error.
- Component counts are not gesture counts. One gesture can contain multiple positive- and negative-Doppler components.
- The metrics describe full recordings. Training examples must later be created as fixed 256×15 numeric windows.

> **Model-pilot update:** shift 6 was acceptable for this small filter matrix,
> but it did not transfer safely to the later `empty`/`clicking_hand` pilot.
> Across those 60 captures, shift 6 clipped 499,927 samples, shift 5 clipped
> 44,983, and shift 4 clipped only 6. Model-pilot export therefore defaults to
> `--diff-shift 4`; this does not alter this historical filter-experiment result.

## Reproduce the difference/shift/clustering evaluation

```powershell
$rawRoot = (Resolve-Path ".\dataset\filter-experiments\raw\fs2000").Path
$outRoot = ".\dataset\filter-experiments\spectrogram\fs2000\difference-shift6\cluster-t12-p8"

Get-ChildItem $rawRoot -Recurse -Filter *.csv |
  Sort-Object FullName |
  ForEach-Object {
    $relativeDirectory = $_.DirectoryName.Substring($rawRoot.Length).TrimStart("\")
    $destination = Join-Path $outRoot $relativeDirectory
    New-Item -ItemType Directory -Force -Path $destination | Out-Null

    python3.11 .\filter_candidate_check.py $_.FullName `
      --clutter-filter difference `
      --diff-shift 6 `
      --fft-size 256 `
      --hop 128 `
      --window hann `
      --dc-guard-hz 20 `
      --cluster-threshold-db 12 `
      --cluster-min-pixels 8 `
      --out-dir $destination `
      --force
  }
```

For each capture, `filter_candidate_check.py` writes:

- A three-panel PNG containing the filtered STFT, threshold mask, and cluster-filtered mask
- A `.metrics.json` file containing the candidate parameters, noise threshold, Q15 clipping, component counts, and pixel counts

## Inspect all metrics in PowerShell

```powershell
$metricsRoot = ".\dataset\filter-experiments\spectrogram\fs2000\difference-shift6\cluster-t12-p8"

Get-ChildItem $metricsRoot -Recurse -Filter *.metrics.json |
  ForEach-Object {
    $m = Get-Content $_.FullName -Raw | ConvertFrom-Json
    [PSCustomObject]@{
      Capture = Split-Path $m.source_capture -Leaf
      ClippingPercent = $m.q15_clipped_sample_percent
      KeptComponents = $m.kept_components
      RemovedComponents = $m.removed_components
      KeptPixels = $m.kept_pixels
    }
  } |
  Sort-Object Capture |
  Format-Table -AutoSize
```

## Archive and share the experiment

```powershell
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"

Compress-Archive `
  -Path ".\dataset\filter-experiments\*" `
  -DestinationPath ".\filter-experiments_$stamp.zip"
```

## Model-input warning

Do not train a model from the full-session PNG files. The intended model input
is a numeric 256×15 tensor produced using the same filter, STFT, scaling, DC
handling, and clustering rules used during inference. Source recordings must
be divided into train/validation/test sessions before overlapping windows are
created, preventing adjacent windows from the same recording from leaking
between splits.
