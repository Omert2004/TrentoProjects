# Radar Gesture Raw-Data Recording Guide

This guide defines how to add compatible raw I/Q recordings to the
MSP430FR5994 four-class radar-gesture dataset. Follow the same labels, timing,
folder structure, and validation rules for every contributor.

The raw CSV files are the permanent source dataset. Spectrograms and model
windows are generated later, so do not add axes, colorbars, PNG pixels,
normalization, or floating-point STFT values to the raw-data folders.

## Dataset contract

| Item | Required value |
|---|---|
| Sampling rate | 2,000 I/Q sample pairs per second |
| Serial configuration | 115200 baud, packetized protocol v1 |
| Timed repetitions per gesture capture | 5 |
| Initial recorded idle | 3.0 s |
| Gesture start times | 3, 8, 13, 18, and 23 s |
| Marked action window | 1.0 s per repetition |
| Repetition period | 5.0 s |
| Final recorded idle | 3.0 s |
| Total gesture capture | 27.0 s |
| Empty capture | 20.0 s |
| Preparation countdown | 3 s before recording; not part of the CSV |
| Speed targets | slow 0.75 s, normal 0.50 s, fast 0.25 s |
| Distances | `near`, `mid`, `far`; use physically marked positions |

`timed_pilot_capture.py` uses this one schedule for `clicking_hand`,
`left_horizontal_scroll`, and `right_horizontal_scroll`. This has been checked
against existing clicking and scrolling metadata: both request 27.0 seconds
and use the same five one-second event markers.

The current STFT geometry is FFT 256, hop 128. A `256x15` window therefore
spans 2,048 raw samples, or 1.024 seconds at 2 kHz. The exporter centers one
window on each one-second gesture marker. Each timed gesture capture is thus
intended to provide five individual gesture samples. Empty captures provide
multiple non-overlapping background windows.

The final training exporter will be aligned with the MSP430 LEA fixed-point
STFT. That later change does not require repeating correctly validated raw I/Q
recordings.

## Class definitions

Use these exact folder and command-line labels.

| Model class | Starting position and action | Stored direction |
|---|---|---|
| `empty` | Remain completely outside the radar field for the full capture | none |
| `clicking_hand` | Begin with an open hand; close once into a fist and hold until `STOP` | `open-to-fist` |
| `left_horizontal_scroll` | Begin at the right endpoint; move once right-to-left and hold until `STOP` | `right-to-left` |
| `right_horizontal_scroll` | Begin at the left endpoint; move once left-to-right and hold until `STOP` | `left-to-right` |

For every timed action, reset only after `STOP`. Finish resetting before the
next `START`, then remain still. Do not make a return movement inside the
one-second marked action window.

## Matrix and session split

One complete subject/session contains 30 raw captures:

- `empty`: three nominal speed subsets, no distance: 3 captures.
- `clicking_hand`: three speeds by three distances: 9 captures.
- `left_horizontal_scroll`: three speeds by three distances: 9 captures.
- `right_horizontal_scroll`: three speeds by three distances: 9 captures.

Five complete sessions contain 150 captures. Each gesture class then has 225
marked events: 5 sessions x 9 captures x 5 repetitions.

| Sessions | Intended split |
|---|---|
| `session01`, `session02`, `session03` | Training: 60% |
| `session04` | Validation: 20% |
| `session05` | Test: 20% |

Never split repetitions from one raw recording across different dataset
splits. Assign the source session first, then extract model windows.

The current `subject01` pilot context is:

| Session | Posture | Hand height |
|---|---|---|
| `session01` | standing | high |
| `session02` | standing | high |
| `session03` | seated | low |
| `session04` | seated | low |
| `session05` | standing | high |

When completing an existing session, match its original posture and hand
height. A new contributor should use a unique subject ID such as `subject02`
and document their posture, hand height, radar placement, and marked physical
distances in session metadata. For deployment-focused data, record all four
classes under the same seated setup rather than mixing posture by class.

## Required files and setup

Keep the compatible versions of these files together:

- `main.c`, `radar_configuration.c`, and `radar_configuration.h`
- `raw_protocol.py`, `raw_serial_capture.py`, and `raw_data.py`
- `timed_pilot_capture.py` and `rate_check.py`
- `capture_model_pilot_matrix.ps1`
- `capture_remaining_scroll_sessions.ps1`

Build and flash the packetized 2 kHz raw-capture firmware before recording.
The Python `--sampling-rate` argument validates the stream; it does not change
the MCU timer.

Install the host dependencies from the `Raw_Data_Capture` directory:

```powershell
python3.11 -m pip install -r .\requirements.txt
```

Close CCS serial terminals, PuTTY, serial monitors, and other programs that
may hold the COM port.

Validate the stream before a recording block:

```powershell
python3.11 .\rate_check.py `
  --port COM7 `
  --baud 115200 `
  --sampling-rate 2000 `
  --duration 15
```

Proceed only when this reports `PASS`, approximately 2,000 samples/s, and zero
CRC, packet-sequence, sample-index, and MCU-drop errors.

## Single-capture commands

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

Empty capture, nominal slow subset:

```powershell
python3.11 .\timed_pilot_capture.py `
  --port COM7 `
  --gesture-class empty `
  --speed slow `
  --subject subject01 `
  --session session01 `
  --out dataset
```

Do not pass `--distance` for `empty`. Its folder uses `na`.

## Complete a new four-class session

For a new contributor or a new complete session, run:

```powershell
.\capture_model_pilot_matrix.ps1 `
  -Python python3.11 `
  -Port COM7 `
  -Baud 115200 `
  -Subject subject02 `
  -Session session01 `
  -OutputRoot dataset
```

Repeat for sessions 02 through 05. The script skips already validated matrix
cells. Do not reuse another person's subject ID.

## Complete the missing scrolling classes in the current pilot

The current `subject01` dataset already contains validated `empty` and
`clicking_hand` recordings in sessions 01 through 05. Run the dedicated script
to add only the missing scrolling classes:

```powershell
.\capture_remaining_scroll_sessions.ps1 `
  -Python python3.11 `
  -Port COM7 `
  -Baud 115200 `
  -Subject subject01 `
  -OutputRoot dataset
```

The script performs one rate check and then records:

```text
session01: all 9 left combinations, then all 9 right combinations
session02: all 9 left combinations, then all 9 right combinations
session03: all 9 left combinations, then all 9 right combinations
session04: all 9 left combinations, then all 9 right combinations
session05: all 9 left combinations, then all 9 right combinations
```

Within one action and distance, the order is `slow`, `normal`, `fast`. Keeping
the same action together helps the operator compare and reproduce speed. The
script pauses at each session and class boundary and skips validated takes when
restarted.

To capture only selected sessions:

```powershell
.\capture_remaining_scroll_sessions.ps1 `
  -Port COM7 `
  -Subject subject01 `
  -Sessions session03,session04 `
  -OutputRoot dataset
```

## Output layout

```text
dataset/
  model-pilot/
    capture_manifest.jsonl
    session_context.json
    raw/
      fs2000/
        subject01/
          session01/
            empty/<speed>/na/*.csv
            empty/<speed>/na/*.metadata.json
            clicking_hand/<speed>/<distance>/*.csv
            clicking_hand/<speed>/<distance>/*.metadata.json
            left_horizontal_scroll/<speed>/<distance>/*.csv
            left_horizontal_scroll/<speed>/<distance>/*.metadata.json
            right_horizontal_scroll/<speed>/<distance>/*.csv
            right_horizontal_scroll/<speed>/<distance>/*.metadata.json
```

Every valid take consists of one CSV and one matching `.metadata.json` file.
The metadata records the exact event markers, class, speed, direction,
distance, sampling rate, file hash, and transport statistics.

## Acceptance and recovery rules

A usable take must end with:

```text
Capture validation: PASS
```

Reject and repeat a take if any of these occur:

- CRC failure, packet-sequence gap, sample-index gap, or MCU drop.
- Incorrect action, direction, speed, distance, posture, or hand height.
- Movement begins before `START`, continues after `STOP`, or includes a reset
  motion inside the marked window.
- Another person enters the radar field during a capture.
- More than one validated CSV/metadata pair exists for the same matrix cell.

Failed captures are retained for diagnosis but must not be used for training.
After a failure, fix the problem and rerun the same loop command. Validated
takes are skipped automatically.

Do not rename, edit, trim, resample, or manually concatenate the raw CSV files.
Do not move a capture to a different class folder to correct a performance
mistake; record the take again with the correct label.

## Package raw data for review

Package a complete subject without duplicating generated spectrograms:

```powershell
$source = Resolve-Path `
  ".\dataset\model-pilot\raw\fs2000\subject01"

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$zipPath = "subject01_model_pilot_raw_$timestamp.zip"

tar.exe -a -c -f $zipPath `
  -C (Split-Path $source -Parent) `
  (Split-Path $source -Leaf)

if ($LASTEXITCODE -ne 0) {
    throw "ZIP creation failed."
}

Get-Item $zipPath | Select-Object FullName, Length
```

Include `capture_manifest.jsonl` and the relevant session-context notes when
submitting data for final audit. Do not place generated `windows`,
`spectrogram-visualizations`, PNG files, or previous ZIP archives inside the
raw-data archive.

## Before model training

Raw collection and feature generation are separate stages. After the complete
raw matrix passes audit:

1. Confirm Q15 difference-filter shift 4 remains safe across all captures.
2. Align the PC feature exporter with the MSP430 LEA fixed-point STFT.
3. Compare PC and board output on identical raw samples.
4. Freeze tensor orientation, integer magnitude/log representation, and model
   scaling.
5. Export train/validation/test tensors by source session.

The existing floating-point spectrogram PNGs remain useful for human
inspection, but they are not the final embedded model inputs.

