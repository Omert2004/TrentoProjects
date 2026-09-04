# TinyCNN 8/16 deployment for MSP430FR5994

This package replaces only the CNN implementation and its weights in the
existing `Model_Phase` project. It does **not** change ADC capture, I/Q wiring,
first-difference clutter cancellation, the Q15 Hann/LEA STFT, the
`spectrogram[15][256]` layout, or the D4 result protocol.

## Locked model and preprocessing contract

| Property | Locked value |
|---|---|
| Checkpoint | corrected ten-session TinyCNN, seed 42, best epoch 46 |
| Input | `1 x 256 x 15`, unsigned integer magnitudes 0..31 expected |
| Sampling | 2,000 Hz |
| STFT | FFT 256, hop 128, Q15 Hann, fixed-scaled LEA FFT |
| Filtering | first difference, `DIFF_SHIFT=4` |
| Time window | 15 STFT columns; training events offset by +512 ms |
| Classes | 0 left, 1 right, 2 clicking hand, 3 empty |
| Model | central 128-bin crop, initial pool, Conv2x2(8), pool, Conv2x2(16), pool, FC(512,4) |
| Parameters | 2,620 |
| Nominal work | 83,968 MACs per inference |

## Verified results

The float checkpoint reproduces the recorded validation result exactly.
The integer implementation uses unsigned 8-bit ReLU activations, signed
10-bit symmetric weights stored as `int16_t`, and `int32_t` accumulators.
The activation ranges were calibrated from validation **inputs only** using
observed min/max; labels were not used to tune the ranges.

| Implementation | Validation accuracy | Validation macro-F1 |
|---|---:|---:|
| Float checkpoint | 85.00% | 84.88% |
| Integer C model | 85.83% | 85.73% |

The small numerical increase is quantization noise, not evidence that the
integer model is better. Integer and float predictions agree on 238/240
validation tensors. The portable C code matches the NumPy integer reference
exactly on all 240/240 tensors, including all four logits. The reserved test
set was not evaluated.

## Memory and expected latency

| Item | Size |
|---|---:|
| Quantized weights | 5,184 bytes in FRAM |
| Int32 biases | 112 bytes in FRAM |
| CNN activation scratch | 96 bytes in RAM |
| Output logits | 16 bytes on the caller stack |

The old 16/32 CNN required roughly 4.7 million MACs and measured about
43.9 seconds for inference on the 8 MHz MSP430 build. Pure MAC scaling gives
about 0.78 seconds for this 83,968-MAC model. Loop, FRAM, pooling, and function
overheads mean the sensible initial expectation is approximately **0.8 to
1.5 seconds per CNN inference**. This is an estimate; measure the flashed
firmware rather than reporting it as a final latency. Including acquisition
of a fresh 15-column window, D4 results should initially be expected roughly
every 2 to 3 seconds.

## Files to replace

Back up and then replace exactly these four files in `Model_Phase`:

| Package file | Destination |
|---|---|
| `inc/radar_cnn.h` | `Model_Phase/inc/radar_cnn.h` |
| `inc/cnn_weights.h` | `Model_Phase/inc/cnn_weights.h` |
| `src/radar_cnn.c` | `Model_Phase/src/radar_cnn.c` |
| `src/cnn_weights.c` | `Model_Phase/src/cnn_weights.c` |

No change to `main.c`, `STFT.c`, `STFT.h`, or `radar_configuration.c` is
required if the current project already calls:

```c
prediction = radar_cnn_classify(&spectrogram[0][0], logits);
```

and sends the result with `UART_putCnnResultFrame(...)`.

## Controlled installation

First create a recoverable backup in PowerShell:

```powershell
$project = "C:\Users\Oguzm\OneDrive - ozyegin.edu.tr\Desktop\Github_Projects\TrentoProjects\Model_Phase"
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backup = Join-Path $project "deployment_backups\before_tinycnn_8x16_$timestamp"

New-Item -ItemType Directory -Force -Path "$backup\inc", "$backup\src" |
    Out-Null

Copy-Item "$project\inc\radar_cnn.h" "$backup\inc\radar_cnn.h"
Copy-Item "$project\inc\cnn_weights.h" "$backup\inc\cnn_weights.h"
Copy-Item "$project\src\radar_cnn.c" "$backup\src\radar_cnn.c"
Copy-Item "$project\src\cnn_weights.c" "$backup\src\cnn_weights.c"

Write-Host "Backup: $backup" -ForegroundColor Green
```

Download the four replacement files and put them in the destination paths
shown above. Then verify their architecture before opening CCS:

```powershell
Select-String "$project\inc\cnn_weights.h" -Pattern `
    "cnn_conv1_weight\[32\]", `
    "cnn_conv2_weight\[512\]", `
    "cnn_fc_weight\[2048\]"

Select-String "$project\src\radar_cnn.c" -Pattern `
    "CNN_CONV1_CHANNELS       8", `
    "CNN_CONV2_CHANNELS      16", `
    "CNN_FC_FEATURES        512"
```

Each command should return all three requested lines.

## CCS build and board test

1. Open the existing `Model_Phase` project in Code Composer Studio.
2. Use **Project > Clean**, then **Project > Build Project**.
3. Confirm that `src/radar_cnn.c` and `src/cnn_weights.c` are compiled.
4. Check `Debug/Model_Phase.map`: RAM, `.leaRAM`, stack, and FRAM must fit.
5. Flash the MSP430FR5994.
6. Reset the board once after flashing.
7. Run the existing result monitor:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
& "$env:USERPROFILE\venvs\modelzoo\Scripts\Activate.ps1"

Set-Location "C:\Users\Oguzm\OneDrive - ozyegin.edu.tr\Desktop\Github_Projects\TrentoProjects\Model_Phase\New Python Files"

python -u .\cnn_result_monitor.py --port COM7 --duration 120 |
    ForEach-Object {
        "$(Get-Date -Format 'HH:mm:ss.fff')  $_"
    }
```

Do not run two serial readers at once. Close the monitor before running an ADC
rate script. In inference firmware, ADC sampling intentionally pauses during
classification, so a continuous-rate test is not the primary pass/fail test.

## Acceptance checks

Record these before changing anything else:

- CCS build has no errors or new memory overflow.
- D4 results arrive without watchdog resets.
- Time between consecutive D4 lines is measured for at least ten results.
- Empty gives mostly `empty` predictions when nobody moves.
- Each gesture is repeated at the same near/mid distances and speeds used in
  collection.
- No test-set evaluation is performed yet.

If D4 output is absent, temporarily retain the existing D0/D2/D3 telemetry to
locate whether acquisition, STFT, or inference stopped. Remove telemetry only
after the new CNN is stable.

## Reproducibility files

`deployment/model_source/` contains the checkpoint, model/loader YAML files,
and recorded float validation metrics. `tools/export_tinycnn_to_c.py`
regenerates the weights without PyTorch. `tools/verify_tinycnn_host_parity.py`
and `deployment/tinycnn_host_runner.c` reproduce the independent C parity
test. Detailed results are in:

- `deployment/tinycnn_quantization_report.json`
- `deployment/tinycnn_host_parity_report.json`

