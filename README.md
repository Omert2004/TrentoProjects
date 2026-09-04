# Energy-Aware Radar Gesture Recognition on MSP430FR5994

This repository family contains an end-to-end research prototype for radar
gesture recognition on a resource-constrained microcontroller. It covers raw
I/Q acquisition, dataset creation, fixed-point STFT validation, neural-network
training, integer deployment, and live inference on the MSP430FR5994.

The current system recognizes four classes:

| ID | Class | Meaning |
|---:|---|---|
| 0 | `left_horizontal_scroll` | Hand movement from right to left |
| 1 | `right_horizontal_scroll` | Hand movement from left to right |
| 2 | `clicking_hand` | Open hand closing into a fist |
| 3 | `empty` | No intentional gesture in the radar field |

The project currently runs as a validated research prototype. The next major
goal is to convert it into a capacitor-powered, event-driven system that can
survive power interruptions and resume computation from MSP430 FRAM.

## Repository family

The folders are separate because they use different firmware builds and serve
different stages of the workflow. Only one firmware build is flashed on the
MSP430 at a time.

| Project | Purpose | Use it when you need to... |
|---|---|---|
| `Raw_Data_Capture` | Continuous packetized raw IFI/IFQ acquisition | verify the 2 kHz ADC stream, record new gesture sessions, create metadata and manifests, inspect raw signals, generate spectrograms, or export tensors |
| `AI_Phase` | Continuous on-device Q15 STFT development and telemetry | inspect the MSP430/LEA spectrogram stream, measure ADC/STFT/DMA timing, or debug signal processing before classification |
| `STFT_Parity` | One-shot stage-by-stage STFT comparison | compare exact board intermediates with Python and localize fixed-point differences; this is diagnostic firmware, not normal runtime firmware |
| `modelzoo` | PC-side model training and evaluation | load radar tensors, apply training-only augmentation, train CNN/FF models, compare seeds, create confusion matrices, inspect errors, and export a selected checkpoint |
| `Model_Phase` | Final embedded inference prototype | run the complete ADC → STFT → TinyCNN pipeline and receive live gesture names from the board |

## Systematic workflow

The main development path is linear. Each stage has a validation gate that
must pass before its output is used by the next stage.

| Stage | Project or tool | Input | Main action | Required output or gate |
|---:|---|---|---|---|
| 1 | `Raw_Data_Capture` firmware and `rate_check.py` | Correctly wired IFI/IFQ radar | Verify ADC pins, packet integrity, and the 2 kHz complex sampling rate | Rate and transport checks pass with no CRC, sequence, or MCU-drop errors |
| 2 | `timed_pilot_capture.py` | Validated acquisition system | Record the required classes, speeds, distances, subjects, and sessions | One CSV, metadata sidecar, and manifest row per capture |
| 3 | Raw-data review tools | Newly recorded captures | Check file counts, validation fields, signal quality, and suspicious spectrograms | Every capture is explicitly accepted, replaced, or quarantined |
| 4 | `export_embedded_model_windows_10sessions.py` | Accepted raw captures | Reproduce the embedded Q15 STFT, apply the selected 512 ms event offset, exclude far captures, and create fixed splits | Export summary and manifest plus 720 training, 240 validation, and 240 reserved test tensors |
| 5 | `modelzoo` | Exported `1 × 256 × 15` tensors | Apply training-only augmentation, train multiple seeds, and review validation confusion matrices, subsets, and errors | Frozen preprocessing, architecture, label order, and checkpoint selected using validation data only |
| 6 | Model export and integer reference | Frozen checkpoint | Quantize weights and activations, generate C arrays, and compare float, Python-integer, and host-C outputs | Integer implementation matches the reference before board integration |
| 7 | `Model_Phase` | Verified C arrays and embedded preprocessing | Build, flash, and run the complete ADC → STFT → TinyCNN pipeline | Live D4 result frames produce gesture names at the expected interval |

### Diagnostic projects

The following projects support the workflow but are not extra mandatory stages
for every training run:

| Project | Run it when... | What it verifies |
|---|---|---|
| `AI_Phase` | ADC, DMA, LEA/STFT timing, or the continuous column stream needs investigation | Live acquisition rate, packet integrity, processing profiles, and continuous Q15 STFT output |
| `STFT_Parity` | The sampling rate, differencing, Q15 scaling, Hann window, FFT, feature transform, or orientation changes | Stage-by-stage agreement between the MSP430 and Python, and the first point at which the two pipelines diverge |

After a wiring or recording change, restart at Stage 1. After a feature-contract
change, rerun `STFT_Parity`, regenerate the tensors, and retrain. A model-only
change normally starts at Stage 5, followed by Stages 6 and 7. The reserved
test split remains untouched until the complete deployable pipeline is frozen.

`Raw_Data_Capture`, `AI_Phase`, `STFT_Parity`, and `Model_Phase` contain
different `main.c` and protocol implementations. Do not mix their firmware and
Python tools. Always clean and rebuild the intended Code Composer Studio
project after changing builds.

## Shared hardware setup

All four MSP430 projects use the same radar and microcontroller connection.
The firmware and host-side Python program change between projects, but the
basic physical wiring remains the same.

### Required equipment

- TI MSP-EXP430FR5994 LaunchPad with an MSP430FR5994 microcontroller.
- Infineon BGT60LTR11AIP 60 GHz Doppler radar board.
- Jumper wires for the two analog outputs, power, and common ground.
- A USB cable for programming, debugging, power, and the LaunchPad UART
  backchannel.
- A Windows computer with Code Composer Studio and the Python environment
  described by the individual project README.
- A stable radar mount and a clearly defined gesture area so distance and
  orientation remain repeatable during recording and live tests.

### Shared wiring

| Radar signal | MSP430FR5994 connection | Firmware function |
|---|---|---|
| `IFI` | `P3.0 / A12` | ADC sequence channel 0; in-phase input |
| `IFQ` | `P3.1 / A13` | ADC sequence channel 1; quadrature input |
| `GND` | LaunchPad `GND` | Common electrical reference |
| Radar supply | Regulated supply required by the radar board configuration | Radar power; verify voltage and polarity before connecting |
| LaunchPad USB | PC USB port | CCS programming/debugging and UART backchannel |

The firmware uses `P2.0 / UCA0TXD` and `P2.1 / UCA0RXD` for the LaunchPad
backchannel UART. These normally route through the LaunchPad debugger, so they
do not need to be wired to the radar.

> **Critical wiring check:** connect radar `IFI` only to MSP430 `A12` and radar
> `IFQ` only to MSP430 `A13`. Do not connect either signal to a no-connect pin.
> Swapping or omitting one channel can produce plausible-looking but physically
> incorrect, mirrored, or unusually symmetric spectrograms and makes the
> resulting dataset invalid.

### Setup and validation order

1. Disconnect power before changing any jumper wire.
2. Verify `IFI`, `IFQ`, supply, and common ground against the table above.
3. Fix the radar position and mark the intended near, mid, and far measurement
   positions used by the dataset protocol.
4. Connect the LaunchPad to the PC and identify its UART COM port.
5. Open the intended CCS project, then clean, rebuild, and flash it.
6. Activate the Python environment and use only the host tools belonging to
   the flashed firmware project.
7. Run the project's ADC-rate and packet-integrity test before collecting data
   or interpreting model output.
8. Start capture, parity analysis, STFT diagnostics, or inference only after
   the validation reports pass.

The capacitor, energy-harvesting source, voltage monitor, and intermittent-
computing circuitry belong to the planned low-power extension. They are not
part of the currently validated USB-powered prototype and should be documented
separately when their electrical design is finalized.

## Current hardware and signal-processing contract

- Microcontroller: TI MSP430FR5994, using its 16-bit MSP430X CPU and LEA
  accelerator.
- Radar input: analog IFI and IFQ channels connected to ADC A12 and A13.
- Sampling rate: 2,000 complex I/Q samples per second.
- Clutter suppression: first difference independently applied to I and Q.
- Difference scaling: `DIFF_SHIFT = 4`, with Q15 saturation.
- Window: the same 256-coefficient Q15 Hann table in Python and firmware.
- FFT: 256-point fixed-scale complex Q15 FFT using MSP-DSPLib/LEA.
- Hop: 128 samples, corresponding to 64 ms and 50% overlap.
- Feature: `fftshift(floor(log2(I² + Q²)))`, stored in the range 0–31.
- Model window: 15 STFT columns.
- Host tensor shape: `1 × 256 × 15` in channel/frequency/time order.
- UART: 115200 baud with CRC-protected diagnostic and result frames.

These values form one contract. Changing the sampling rate, Q15 shift, window,
FFT scaling, input orientation, crop, label order, or normalization requires
regenerating the affected tensors and verifying or retraining the model.

## Dataset summary

The corrected pilot dataset contains ten recording sessions from one subject.
The first five sessions include near, mid, and far captures. Sessions 06–10
were re-recorded after an IFI/IFQ wiring problem was identified; the invalid
versions were quarantined instead of silently reused. The corrected later
sessions contain the near and mid variants used by the final model.

The complete raw collection contains 255 validated capture records:

- Sessions 01–05: 30 captures per session.
- Sessions 06–10: 21 captures per session.
- Gesture captures contain five marked actions.
- Empty captures provide deterministic background windows.
- Every CSV has a metadata sidecar and a manifest entry.
- Captures are rejected when CRC, sequence, rate, or MCU-drop validation fails.

The selected 512 ms offset, far-excluded export contains 1,200 tensors:

| Split | Sessions | Per class | Total |
|---|---|---:|---:|
| Training | 01, 02, 03, 06, 07, 08 | 180 | 720 |
| Validation | 04, 09 | 60 | 240 |
| Test | 05, 10 | 60 | 240 |
| **Total** | **10 sessions** | **300** | **1,200** |

Training-only time shifting and range masking create one dynamic augmented copy
per training tensor, so the loader presents 1,440 training examples. Validation
and test tensors are never augmented. Spectrogram time reversal was evaluated
and rejected because it reduced accuracy and did not reliably transform a left
gesture into a physically valid right gesture.

The reserved test split has not been used for model selection and has not yet
been reported as a final result.

## Main achievements

### Reliable acquisition and reproducible data

- Implemented a CRC-protected packet protocol with sequence, sample-index, and
  cumulative-drop checks.
- Sustained approximately 1,992 accepted samples/s against the 2 kHz target
  during validated captures, with zero parser resynchronizations and zero MCU
  drops in the accepted recordings.
- Added guided capture timing, action markers, session-aware folder layout,
  metadata sidecars, manifests, and deterministic train/validation/test export.
- Detected the incorrect IFI/IFQ wiring through spectrogram and I/Q diagnostics,
  quarantined the affected data, and re-recorded the invalid sessions.
- Added confusion-matrix, speed/distance subset, and misclassified-tensor review
  tools instead of treating every model error as a learning failure.

### Fixed-point STFT validation

- The Python and MSP430 implementations use the same first difference, Q15
  scaling, Hann coefficients, FFT geometry, magnitude compression, and FFT
  shift.
- The Q15 scaling/window stage matched exactly for all 512 transmitted I/Q
  components in the parity experiment.
- Starting from the board's FFT output, magnitude, integer log2, and fftshift
  matched exactly for all 256 bins.
- The remaining host-versus-board difference was localized to the internal
  LEA/DSPLib FFT rounding behavior, rather than UART corruption, windowing, or
  final postprocessing. The exported tensors are therefore LEA-aligned
  approximations, not falsely claimed to be bit exact.

### Model development

The final comparison used the corrected ten-session dataset and training-only
time-shift/range-mask augmentation.

| Model | Validation accuracy | Validation macro-F1 | Deployment conclusion |
|---|---:|---:|---|
| CNN, 16/32 channels, three-seed mean | 82.36% ± 1.73% | 82.43% ± 1.89% | Accurate but unnecessarily expensive for this MCU |
| FF width 64, seed 42 | 84.17% | 84.23% | Q15 weights do not fit the available FRAM budget |
| **TinyCNN, 8/16 channels, three-seed mean** | **83.75% ± 1.10%** | **83.59% ± 1.18%** | **Selected deployable architecture** |
| TinyCNN, seed 42 checkpoint | 85.00% | 84.88% | Exported for deployment |

Seeds 7, 42, and 123 were used to measure stability. Checkpoints were selected
using validation macro-F1, with validation loss as the tie-break. The best seed
is not presented as the expected performance of the complete training method.

### Embedded TinyCNN deployment

- Architecture: two 2×2 convolution stages with 8 and 16 channels, ReLU,
  max-pooling, and a four-class fully connected output.
- Parameters: 2,620.
- Complexity: 83,968 multiply-accumulate operations per inference.
- Quantized model data: approximately 5.3 kB of integer weights and biases.
- Streamed activation scratch: 96 bytes.
- Exact host Python/integer-C logit agreement: 240/240 validation tensors.
- Integer validation result for the exported seed-42 model: 85.83% accuracy and
  85.73% macro-F1.
- Live MSP430 results arrive approximately once every 2.0 seconds.
- The PC monitor reports local time, the firmware inference sequence, and a
  readable detected-action name. Raw logits are optional debugging output.

The reference linker map reports 3,684 of 4,096 bytes of general RAM used,
2,048 bytes of LEA RAM used, and 23,126 bytes of combined FRAM/FRAM2 used.
General RAM, not model storage, is the tightest remaining resource.

## Current limitations

- The dataset contains only one subject and a limited number of environments.
- Validation and test sessions are independent sessions, but they are still
  from the same person.
- Far-distance captures were excluded from the final training dataset because
  they often lacked a clear gesture response.
- Left/right scrolling remains the most difficult class boundary.
- The classifier is closed-set: every window must become one of four classes.
  A stationary hand, an unknown gesture, or another moving object can therefore
  be reported as a known gesture.
- The current D4 result does not contain a calibrated probability. Integer
  logits are decision scores and must not be displayed as confidence
  percentages without an exported scale and calibration experiment.
- The host FFT approximation is close to, but not bit exact with, the LEA FFT.
- The current firmware sleeps in LPM0 between interrupts, but ADC sampling and
  feature generation continue. It is not yet an energy-neutral or
  intermittently powered system.

## Planned low-power and intermittent-computing system

The long-term goal is to avoid running the complete STFT and CNN continuously.
The radar system should remain in a very low-power waiting state, wake when
motion is detected, acquire a bounded gesture window, classify it, report the
result, and return to sleep.

### 1. Event-driven wake-up

- Use a low-power radar motion/target indication output when the selected radar
  configuration exposes a suitable interrupt signal.
- If a hardware motion output is not usable, run a much cheaper low-rate ADC
  energy detector before enabling the complete 2 kHz STFT pipeline.
- Wake the MSP430 from LPM3/LPM4, power or clock only the required peripherals,
  and enter the full-rate acquisition state only after a motion threshold is
  crossed.
- Add hysteresis and a cooldown period to prevent one gesture from causing
  repeated wake-ups.

### 2. Capacitor-buffered energy operation

The storage capacitor or supercapacitor should be treated as an energy budget,
not only as a voltage source. The usable energy between two voltage thresholds
is

```text
E_available = 1/2 × C × (V_high² − V_low²).
```

Measure the actual energy required for wake-up, radar settling, acquisition,
15 STFT hops, TinyCNN inference, and result transmission. Choose `V_high` so a
started task can finish safely, and choose `V_low` high enough to checkpoint
state before brownout. Add threshold hysteresis so the system does not rapidly
switch between charging and running.

### 3. Intermittent computing with FRAM checkpoints

- Divide the application into bounded, restartable tasks: wake detection,
  acquisition, STFT hop, inference block, result commit, and sleep.
- Save only necessary progress to nonvolatile FRAM: task identifier, valid
  column count, ring/window position, inference sequence, and integrity data.
- Use two checkpoint slots with version numbers and CRCs so a power failure
  during a write cannot destroy the last valid state.
- Make each task idempotent, allowing it to restart without duplicating samples
  or transmitting the same result twice.
- On reboot, validate the newest checkpoint and either resume safely or discard
  an incomplete gesture and return to the waiting state.
- Measure checkpoint energy and FRAM-write frequency; checkpointing too often
  can consume more energy than recomputing a small task.

### 4. Production communication mode

The current D0 spectrogram stream is valuable for debugging but unnecessary for
normal embedded classification. A production mode should transmit only compact
status and D4 result packets, disable continuous profiling unless requested,
and keep UART off while waiting for motion.

### 5. Model and dataset improvements

- Add `stationary_hand` or `unknown_motion` data, or place an activity detector
  before the four-class classifier.
- Collect data from multiple people, rooms, radar placements, postures, hand
  heights, and lighting/electrical-noise conditions.
- Add more difficult empty/background examples and moving-object negatives.
- Keep validation and test splits grouped by subject/session/environment.
- Calibrate confidence or rejection thresholds on a separate calibration set.
- Evaluate the reserved test set once after the event detector, preprocessing,
  quantization, and model are frozen.

## Recommended development order

1. Preserve the current working firmware and its linker map as the baseline.
2. Measure current and energy for waiting, ADC, STFT, CNN, and UART phases.
3. Add motion-triggered wake and verify that classification remains unchanged.
4. Disable high-bandwidth debug telemetry in a separate production build.
5. Add capacitor voltage sensing and energy-aware start/stop thresholds.
6. Implement atomic FRAM checkpoints and forced-power-failure tests.
7. Expand the dataset and add unknown/stationary-hand handling.
8. Freeze the complete pipeline and evaluate the reserved test set once.

Low-power changes should be introduced independently from model or signal-
processing changes. Each stage should be compared with the present continuous
baseline for accuracy, latency, memory, energy, and recovery correctness.

## Documentation roadmap

This file provides the project-level overview. Each project should also have a
dedicated technical README:

- `Raw_Data_Capture/README.md`: wiring, firmware build, serial protocol,
  environment setup, collection matrix, validation, and dataset expansion.
- `AI_Phase/README.md`: Q15 STFT implementation, LEA memory requirements,
  telemetry protocol, profiling, and continuous spectrogram tools.
- `STFT_Parity/README.md`: diagnostic build configuration, stage packets,
  Python comparison, and interpretation of mismatch reports.
- `modelzoo/README_RADAR.md`: tensor installation, loader configuration,
  augmentation, training, multi-seed evaluation, error inspection, and export.
- `Model_Phase/README.md`: final firmware integration, quantization contract,
  CCS setup, memory map, flashing, monitoring, and board validation.

Those technical READMEs should reference this overview for project history and
scope instead of duplicating all experimental results.

## Attribution and acknowledgements

### University of Trento model-training framework

The PC-side `modelzoo` workflow used in this project is based on the University
of Trento's [`tinysystems/modelzoo`](https://github.com/tinysystems/modelzoo/)
repository. It provided the configurable PyTorch and Hydra training structure
on which the radar-specific work was built.

This internship project added or adapted the radar tensor loader, training-only
augmentation, radar experiment configuration, validation reports, error and
subset analysis, TinyCNN architecture, quantization checks, and MSP430 export
path. These additions should not be interpreted as authorship of the original
training framework.

When redistributing this work, retain the upstream copyright and license files,
link to the original repository, and record the exact upstream commit used. If
the upstream project or an associated publication provides a requested citation,
include that citation as well. The local checkout can record its exact origin
with:

```powershell
git -C .\modelzoo remote -v
git -C .\modelzoo rev-parse HEAD
```

### Internship acknowledgement

This project was developed during a research internship at the
[University of Trento](https://www.unitn.it/en). I gratefully thank
[Prof. Kasım Sinan Yıldırım](https://sinanyil81.github.io/) and
[Beran Kılıç](https://github.com/berab) for their supervision, technical
guidance, feedback, and support throughout the internship.

## Status

The project has demonstrated a complete path from validated raw radar capture
to live integer neural-network inference on an MSP430FR5994. The selected model
fits the device and produces stable live classifications at the intended
cadence. Low-power wake-up, capacitor-aware scheduling, brownout-safe
checkpointing, multi-subject generalization, and final test evaluation remain
future work.
