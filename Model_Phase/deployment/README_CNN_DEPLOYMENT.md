# Embedded radar CNN

This project embeds the corrected ten-session, seed-42 CNN in the
MSP430FR5994 `AI_Phase` firmware.

## Model identity and result

The original checkpoint is `deployment/model_source/state_dict.pt`:

```text
SHA-256: 73200a910ffc657cdc10597f40833a7e8d6f680bb6732805212608ee22098ac7
```

| Implementation | Validation accuracy | Validation macro-F1 |
|---|---:|---:|
| Float checkpoint | 83.75% | 83.90% |
| Integer deployment | 84.17% | 84.31% |

The implementations agree on 239/240 validation predictions. The reserved
test set remains untouched. Across seeds 7, 42 and 123, the corrected float
CNN achieved 82.36% +/- 1.73% accuracy and 82.43% +/- 1.89% macro-F1.

## Exact contract

| Property | Value |
|---|---|
| Sampling | 2000 Hz |
| Preprocessing | first difference, Q15 saturation, `DIFF_SHIFT=4` |
| STFT | FFT 256, hop 128, Q15 Hann, fixed-scaled LEA FFT |
| Tensor | 256 frequency bins x 15 time frames |
| Board layout | `spectrogram[15][256]` |
| Input | raw integers 0..31; no normalization |
| Classes | 0 left, 1 right, 2 clicking hand, 3 empty |

`inc/STFT.h` uses `ENABLE_CLUTTER_CANCEL=1` and `DIFF_SHIFT=4`, matching the
training exporter.

## Memory design

```text
1x256x15 -> Conv3x3(16) -> ReLU -> MaxPool2
          -> Conv3x3(32) -> ReLU -> MaxPool2 -> Linear(6144,4)
```

The deployment uses int8 weights, uint8 activations and int32 accumulators.
Convolution, ReLU and pooling are fused, so complete activation maps are never
stored. Only four first-pool rows remain in memory at once.

| Item | Size |
|---|---:|
| Weights in FRAM | 29,328 bytes |
| Int32 biases in FRAM | 208 bytes |
| Incremental LEA-RAM scratch | 448 bytes |
| Output logits | 16 bytes |
| Work per inference | approximately 4.6 million MACs |

Relevant files are `src/radar_cnn.c`, `src/cnn_weights.c`, their headers under
`inc/`, and the reproducible exporter and manifests in `deployment/`.

## Runtime behavior

The first STFT column after reset is warm-up because half its window is zero.
The next 15 complete columns form the CNN input. The firmware stops Timer_A2,
waits for ADC/DMA completion, classifies, sends a result, clears acquisition
history and resumes sampling.

Sampling pauses deliberately during inference. The CNN cannot finish within
the 512-sample ring-buffer duration; continuing would silently drop samples.

CNN results use the CRC-protected `D4` packet:

```text
AA 55 D4 inference_seq:u16 last_column_seq:u16 predicted_class:u8
         logits[4]:i32 crc16:u16
```

`New Python Files/stft_protocol.py` supports this packet and
`cnn_result_monitor.py` prints predictions and logits.

## Build and flash

1. Import `AI_Phase` into Code Composer Studio.
2. Select Debug, then use **Project > Clean** and **Project > Build Project**.
3. Confirm `src/radar_cnn.c` and `src/cnn_weights.c` were compiled.
4. Inspect the new `Debug/AI_Phase.map`: ordinary RAM and `.leaRAM` must fit;
   code/constants must remain below `0x10000` under the restricted data model;
   stack headroom must remain positive.
5. Flash the MSP430FR5994.

The portable CNN kernel compiles cleanly with strict host warnings and matches
a separate NumPy integer implementation exactly. TI's compiler is unavailable
in the packaging environment, so the CCS link/map check is mandatory.

## Read board predictions

```powershell
Set-ExecutionPolicy -Scope Process Bypass
& "$env:USERPROFILE\venvs\modelzoo\Scripts\Activate.ps1"

Set-Location "C:\Users\Oguzm\OneDrive - ozyegin.edu.tr\Desktop\Github_Projects\TrentoProjects\AI_Phase\New Python Files"

python .\cnn_result_monitor.py --port COM7 --baud 115200 --duration 120
```

Change `COM7` if necessary. `rate_check.py` will not show continuous 2 kHz
operation in inference mode because sampling pauses intentionally.

## Remaining limitations

- The host exporter approximates the LEA FFT but is not bit-exact.
- Training windows were event-aligned at +512 ms; live windows are
  free-running and a gesture can cross a boundary.
- Inference creates acquisition dead time.
- Final test evaluation must wait until this exact firmware is accepted.
