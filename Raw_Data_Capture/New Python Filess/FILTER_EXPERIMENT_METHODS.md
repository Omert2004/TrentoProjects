# 2 kHz Raw-I/Q Filter Experiment

This experiment has one fixed acquisition configuration: **Raw_Data_Capture at
2000 samples/s**. Do not flash a different filter for each recording. Capture
the scene once as validated, unfiltered I/Q and replay the same CSV through
every candidate offline. Only the selected pipeline is later implemented in
`AI_Phase` and verified against the Python reference.

## What is being compared

| Stage | Candidates | Meaning |
|---|---|---|
| Clutter preprocessing | none, capture-mean removal, single-delay difference, first-order high-pass | Operates on complex I/Q before the STFT |
| Difference scaling | `DIFF_SHIFT` 4, 6, 8 initially | Q15 gain after differencing; this is **not another filter** |
| STFT | FFT 256, hop 128, Hann | Held fixed for every candidate |
| Blob cleanup | threshold 6/9/12 dB above noise; minimum 2/4/8 connected pixels | Operates on the STFT mask after a ±20 Hz DC guard |

The existing `spectrogram_view.py` supports the four clutter preprocessors.
`filter_candidate_check.py` adds Q15 shift/clipping checks and an 8-connected
component filter for the clustering experiment.

## Capture matrix

Capture these 13 takes once:

| Condition | Distance(s) | Duration | Method |
|---|---|---:|---|
| Empty scene | n/a | 20 s | Operator outside radar range |
| Stationary hand | near, mid, far | 20 s each | Open hand, fixed position |
| Slow movement | near, mid, far | 30 s each | 5 alternating horizontal passes |
| Normal movement | near, mid, far | 30 s each | 5 alternating horizontal passes |
| Fast movement | near, mid, far | 30 s each | 5 alternating horizontal passes |

For every movement take, hold still for 2 seconds at the beginning and end,
alternate left-to-right/right-to-left, keep the travel distance constant, and
space the five complete passes about 4 seconds apart. Mark the physical near,
mid, and far positions on the table so they do not change between takes.

The recorder stores data as:

```text
dataset/
  filter-experiments/
    raw/
      fs2000/
        empty_scene/na/
        stationary_hand/{near,mid,far}/
        slow_movement/{near,mid,far}/
        normal_movement/{near,mid,far}/
        fast_movement/{near,mid,far}/
    capture_manifest.jsonl
```

Each CSV has a matching `.metadata.json`. The manifest indexes the condition,
distance, speed, direction, subject, hash, sample count, and transport result.

## Commands

Run all commands from the `Raw_Data_Capture` directory.

### 1. Confirm the firmware stream

```powershell
python3.11 .\rate_check.py --port COM7 --baud 115200 --sampling-rate 2000 --duration 15
python3.11 .\frame_timing_check.py --port COM7 --baud 115200 --sampling-rate 2000 --duration 30
```

Both must pass with zero CRC, sequence, sample-index, and MCU-drop errors.

### 2. Capture the complete matrix

The guided script validates the stream, prompts before every physical setup,
stops immediately if a take fails, and records all 13 takes:

```powershell
powershell -ExecutionPolicy Bypass -File .\capture_filter_matrix.ps1 `
  -Python python3.11 -Port COM7 -Subject subject01 -OutputRoot dataset
```

A single manual take uses the same storage rules. Examples:

```powershell
# Empty scene
python3.11 .\raw_serial_capture.py --port COM7 --baud 115200 `
  --sampling-rate 2000 --duration 20 `
  --capture-purpose filter-experiment --condition empty_scene `
  --subject subject01 --out dataset

# Stationary hand, mid
python3.11 .\raw_serial_capture.py --port COM7 --baud 115200 `
  --sampling-rate 2000 --duration 20 `
  --capture-purpose filter-experiment --condition stationary_hand `
  --distance mid --subject subject01 --out dataset

# Slow alternating horizontal movement, far
python3.11 .\raw_serial_capture.py --port COM7 --baud 115200 `
  --sampling-rate 2000 --duration 30 `
  --capture-purpose filter-experiment --condition slow_movement `
  --distance far --direction horizontal-alternating `
  --subject subject01 --out dataset
```

If validation says `FAIL`, retain the file only for diagnosis and repeat that
take. Do not put a failed take into filter selection or the final dataset.

### 3. Inspect the raw captures

```powershell
$rawRoot = ".\dataset\filter-experiments\raw\fs2000"
$empty = (Get-ChildItem "$rawRoot\empty_scene\na\*.csv" | Sort-Object LastWriteTime | Select-Object -Last 1).FullName
$slowFar = (Get-ChildItem "$rawRoot\slow_movement\far\*.csv" | Sort-Object LastWriteTime | Select-Object -Last 1).FullName
$fastNear = (Get-ChildItem "$rawRoot\fast_movement\near\*.csv" | Sort-Object LastWriteTime | Select-Object -Last 1).FullName

python3.11 .\visualizer.py $empty
python3.11 .\visualizer.py $slowFar
python3.11 .\visualizer.py $fastNear
python3.11 .\interference_check.py $empty --sampling-rate 2000 --nperseg 4096
```

These three captures are the screening set: empty scene tests false positives,
slow/far tests weak-motion retention, and fast/near tests clipping.

### 4. Compare clutter filters on identical samples

```powershell
# Baseline
python3.11 .\spectrogram_view.py $slowFar --filter none `
  --fft-size 256 --hop 128 --window hann --out .\filter-results\slow-far_none.png

# Per-capture mean removal
python3.11 .\spectrogram_view.py $slowFar --filter mean `
  --fft-size 256 --hop 128 --window hann --out .\filter-results\slow-far_mean.png

# Single-delay clutter canceller
python3.11 .\spectrogram_view.py $slowFar --filter difference `
  --fft-size 256 --hop 128 --window hann --out .\filter-results\slow-far_difference.png

# First-order high-pass candidates
foreach ($cutoff in 1, 3, 5, 10) {
  python3.11 .\spectrogram_view.py $slowFar --filter highpass --highpass-hz $cutoff `
    --fft-size 256 --hop 128 --window hann `
    --out ".\filter-results\slow-far_highpass-${cutoff}Hz.png"
}
```

Repeat the same commands for `$empty` and `$fastNear`. Keep at most the best
two clutter methods before running the full matrix.

### 5. Tune `DIFF_SHIFT` without changing the capture

```powershell
foreach ($shift in 4, 6, 8) {
  python3.11 .\filter_candidate_check.py $fastNear `
    --clutter-filter difference --diff-shift $shift `
    --cluster-threshold-db 9 --cluster-min-pixels 4 `
    --out-dir ".\filter-results\diff-shift$shift"
}
```

Choose the largest shift that keeps Q15 clipping below **0.1%** in every
fast/near capture. If 6 passes and 8 fails, test 7. Then confirm that chosen
shift preserves the slow/far signal. The final on-chip value still needs the
`AI_Phase_Parity` same-window check because NumPy does not reproduce every LEA
fixed-point rounding step.

### 6. Tune the clustering filter

Use the selected clutter candidate and sweep threshold/minimum area. Example
for single-delay difference with shift 6:

```powershell
foreach ($threshold in 6, 9, 12) {
  foreach ($minPixels in 2, 4, 8) {
    python3.11 .\filter_candidate_check.py $slowFar `
      --clutter-filter difference --diff-shift 6 `
      --cluster-threshold-db $threshold --cluster-min-pixels $minPixels `
      --out-dir ".\filter-results\cluster-t${threshold}-p${minPixels}"
  }
}
```

Run the same sweep on `$empty` first and `$fastNear` second. Reject settings
that leave repeated empty-scene blobs or erase the slow/far movement. Then run
only the remaining setting(s) across all 13 files.

### 7. Full-matrix verification

This example applies the selected difference/shift/clustering setting to every
validated raw CSV:

```powershell
Get-ChildItem .\dataset\filter-experiments\raw\fs2000 -Recurse -Filter *.csv |
  ForEach-Object {
    python3.11 .\filter_candidate_check.py $_.FullName `
      --clutter-filter difference --diff-shift 6 `
      --cluster-threshold-db 9 --cluster-min-pixels 4 `
      --out-dir .\filter-results\selected
  }
```

Replace `6`, `9`, and `4` with the values actually selected during screening.

## Acceptance rules

A candidate is acceptable only when all of these hold:

1. Every source capture has transport validation `PASS` and zero reported MCU drops.
2. Empty-scene isolated components are strongly reduced.
3. Stationary clutter/DC is reduced without creating artificial sidebands.
4. Slow movement at far distance remains visibly connected and repeatable.
5. Fast movement at near distance does not exceed 0.1% Q15 clipping.
6. Directional positive/negative Doppler structure is preserved.
7. One parameter set works at near, mid, and far; do not tune per distance.

After selecting the winner, implement only that pipeline in `AI_Phase`, run
STFT parity, and capture the final gesture dataset. Do not train on the 13
filter-characterization recordings; they are controlled engineering evidence,
not the balanced model dataset.
