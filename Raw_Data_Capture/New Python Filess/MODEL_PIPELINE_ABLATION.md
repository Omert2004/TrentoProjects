# Four-Class Model Pilot and A-D Ablation

This pilot records four model classes while keeping gesture speed as a separate
subset attribute. The model target is **not** the combined class-and-speed
string.

## Label definition

| Model class | Operator action |
|---|---|
| `clicking_hand` | Begin open, close once into a fist, then keep the fist still until `STOP`. |
| `left_horizontal_scroll` | Begin on the right, move once to the left, then hold until `STOP`. |
| `right_horizontal_scroll` | Begin on the left, move once to the right, then hold until `STOP`. |
| `empty` | Stay completely outside the radar range for the whole capture. |

Every class is recorded under `slow`, `normal`, and `fast`. For the three
gesture classes, the requested motion-completion times are:

| Speed subset | Finish the movement in approximately | Remaining marked window |
|---|---:|---|
| `slow` | 0.75 s | Hold the final pose until `STOP` |
| `normal` | 0.50 s | Hold the final pose until `STOP` |
| `fast` | 0.25 s | Hold the final pose until `STOP` |

Each marked action window is 1.0 second. This fits inside one `256x15` model
window, whose raw-sample span is 1.024 seconds at 2 kHz.

`empty` has no physical speed. Its speed value is a nominal subset that creates
three independent noise recordings and keeps the matrix grouped consistently.
The model label remains `empty` for all three.

## Files to update

Place these files together in the existing Python directory:

- `timed_pilot_capture.py`
- `export_model_windows.py`
- `raw_serial_capture.py`
- `filter_candidate_check.py`
- `raw_data.py`
- `raw_protocol.py`
- `spectrogram_view.py`
- `rate_check.py`

`capture_model_pilot_matrix.ps1` is optional. The commands below call Python
directly and give the operator control before every take.

The MSP430 may remain on the validated 2 kHz packetized raw firmware.

## Capture behavior

Every command waits for Enter, counts down `3, 2, 1`, and then begins capture.
Gesture takes provide another three-second initial idle interval before the
first `START`. Each gesture take contains five repetitions, with one marked
1.0-second action window every five seconds.

Between `STOP` and the next `START`, reset the hand to the required beginning
position and remain still.

### Examples

Clicking hand, slow, near:

```powershell
python3.11 .\timed_pilot_capture.py `
  --port COM7 `
  --gesture-class clicking_hand `
  --speed slow `
  --distance near `
  --subject subject01 `
  --session session01 `
  --out dataset
```

Left horizontal scroll, normal, mid:

```powershell
python3.11 .\timed_pilot_capture.py `
  --port COM7 `
  --gesture-class left_horizontal_scroll `
  --speed normal `
  --distance mid `
  --subject subject01 `
  --session session01 `
  --out dataset
```

Right horizontal scroll, fast, far:

```powershell
python3.11 .\timed_pilot_capture.py `
  --port COM7 `
  --gesture-class right_horizontal_scroll `
  --speed fast `
  --distance far `
  --subject subject01 `
  --session session01 `
  --out dataset
```

Empty/noise, nominal slow subset (do not enter radar range):

```powershell
python3.11 .\timed_pilot_capture.py `
  --port COM7 `
  --gesture-class empty `
  --speed slow `
  --subject subject01 `
  --session session01 `
  --out dataset
```

Do not pass `--distance` for `empty`.

### Five-session loop for one matrix cell

This loop still pauses for Enter before each capture, so it does not take away
operator timing control:

```powershell
foreach ($number in 1..5) {
    $session = "session{0:D2}" -f $number

    python3.11 .\timed_pilot_capture.py `
      --port COM7 `
      --gesture-class clicking_hand `
      --speed slow `
      --distance near `
      --subject subject01 `
      --session $session `
      --out dataset

    if ($LASTEXITCODE -ne 0) {
        throw "Capture failed in $session. Stop and repeat that take."
    }
}
```

Change only `--gesture-class`, `--speed`, and `--distance` for another matrix
cell.

## Required matrix

One complete subject/session matrix contains 30 validated recordings:

- `empty`: 3 speed subsets x 1 distance-free recording = 3 takes
- Each gesture class: 3 speeds x 3 distances = 9 takes
- Three gesture classes: 27 takes

Across five sessions, this produces 150 raw recordings. Do not capture
separately for pipelines C and D; both are derived from the same raw files.

Recommended split:

| Sessions | Split |
|---|---|
| `session01`, `session02`, `session03` | Train |
| `session04` | Validation |
| `session05` | Test |

Output layout:

```text
dataset/model-pilot/raw/fs2000/
  subject01/session01/<gesture_class>/<speed>/<distance>/*
```

The distance folder for `empty` is `na`.

## Export paired A-D windows

After all five 30-take matrices pass validation:

```powershell
python3.11 .\export_model_windows.py `
  --input-root .\dataset\model-pilot\raw\fs2000 `
  --out .\dataset\model-pilot\windows `
  --train-sessions session01 session02 session03 `
  --validation-sessions session04 `
  --test-sessions session05 `
  --diff-shift 4 `
  --dc-guard-hz 20 `
  --cluster-threshold-db 12 `
  --cluster-min-pixels 8
```

The exporter rejects incomplete matrices, duplicate validated takes, unknown
classes/speeds, and session overlap. It processes a complete recording before
extracting windows so the difference filter is not reset at window boundaries.

- Pipeline A: ADC-centered raw I/Q, 256-point Hann STFT
- Pipeline B: 10 Hz first-order high-pass, 256-point Hann STFT
- Pipeline C: single-delay difference, `DIFF_SHIFT=4`, 256-point Hann STFT
- Pipeline D: Pipeline C plus causal per-window thresholding and 8-connected clustering

All four outputs use identical window IDs and have shape `256x15` (`float32`). The
output path and manifest store `gesture_class` and `speed` separately:

```text
dataset/model-pilot/windows/
  pipeline-A/<split>/<gesture_class>/<speed>/<distance>/*.npy
  pipeline-B/<split>/<gesture_class>/<speed>/<distance>/*.npy
  pipeline-C/<split>/<gesture_class>/<speed>/<distance>/*.npy
  pipeline-D/<split>/<gesture_class>/<speed>/<distance>/*.npy
  paired_windows_manifest.jsonl
  export_summary.json
```

Do not normalize each window independently. Fit any fixed magnitude scaling on
training data only. Select A-D using validation macro F1, per-class recall,
false positives for `empty`, and recall broken down by speed and distance. Open
the test split only after the pipeline and training settings are frozen.

### Two-class preliminary export

While only `empty` and `clicking_hand` are available, add:

```powershell
  --classes empty clicking_hand `
  --session-context .\dataset\model-pilot\session_context.json
```

The class-subset option still requires every selected speed/distance cell in
every assigned session. Session context is copied into each manifest row so
standing/high and seated/low recordings remain traceable.

The first two-class pilot showed that the earlier filter-experiment choice of
shift 6 does not transfer safely to these captures. Shift 4 is now the default:
it clipped only 6 samples over all 60 captures, while shift 5 and shift 6
clipped 44,983 and 499,927 samples respectively.
