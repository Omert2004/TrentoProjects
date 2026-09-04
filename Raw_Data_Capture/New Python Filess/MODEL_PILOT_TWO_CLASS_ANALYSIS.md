# Empty and Clicking-Hand Model Pilot Analysis

## Outcome

The 60 uploaded captures are intact and were processed successfully. The
dataset is usable as a **two-class pilot**, with an important limitation:
`clicking_hand` is clearly visible at `near`, but the simple event-versus-idle
screen is weak at `mid` and close to noise at `far`. This does not prove that a
trained classifier cannot learn those cases, but it makes a full scrolling
collection at all distances risky before a smaller scrolling pilot is checked.

Pipeline C now uses `DIFF_SHIFT=4`. The former shift-6 choice from the filtering
experiment is not safe for this higher-amplitude model-pilot data. The current
Pipeline D clustering parameters should not be used for training because they
erase almost every clicking window.

## Raw capture audit

| Check | Result |
|---|---:|
| Captures | 60 |
| Clicking action events | 225 |
| Event-marker boundaries | 450 |
| Metadata SHA-256 matches | 60/60 |
| Host transport validation | 60/60 PASS |
| CRC, packet, sample-index, reorder, and MCU-drop errors | 0 |
| Observed receive rate | 1992.12–1994.12 samples/s |
| Mean marker scheduling error | 8.23 ms |
| 95th-percentile marker scheduling error | 17.88 ms |

The marker timing values measure the capture program's scheduling, not the
operator's reaction time after hearing/seeing `START`.

## Processing and split

Every exported tensor is `float32`, shape `256 x 15`, and spans 2,048 raw
samples (1.024 seconds at 2 kHz). The same window IDs are used in all pipelines:

- A: ADC-centered raw complex I/Q plus Hann STFT
- B: 10 Hz first-order high-pass plus Hann STFT
- C: single-delay difference with Q15 shift 4 plus Hann STFT
- D: Pipeline C plus the current threshold/component clustering candidate

| Split | Sessions | Clicking windows | Empty windows | Total per pipeline |
|---|---|---:|---:|---:|
| Train | 01, 02, 03 | 135 | 162 | 297 |
| Validation | 04 | 45 | 54 | 99 |
| Test | 05 | 45 | 54 | 99 |
| **Total** | 01–05 | **225** | **270** | **495** |

There are 495 paired A-D examples and 1,980 `.npy` tensors in total. `empty`
produces 18 non-overlapping interior windows per 20-second take; each clicking
take contributes its five marked repetitions.

These are deterministic, leakage-resistant **provisional** splits. Because all
five session IDs came from one subject and one collection occasion, the test
split is not an independent-subject or independent-day generalization test.

## Difference-filter scaling

| Q15 shift | Total clipped samples | Captures over 0.1% clipping | Worst capture |
|---:|---:|---:|---:|
| 3 | 0 | 0 | 0.0000% |
| **4** | **6** | **0** | **0.0037%** |
| 5 | 44,983 | 53 | 3.6399% |
| 6 | 499,927 | 53 | 39.1252% |

Shift 4 is the largest tested setting that remains safely below the 0.1%
per-capture clipping screen. No raw data were modified; this change affects
derived tensors only.

## Clicking signal screen

For each of the 225 marked actions, an event window was compared with an idle
window from the same capture. A 0.256-second analysis offset was used as a
reaction-latency sensitivity check. Values below are Pipeline C median event
minus idle energy; the percentage is the fraction of repetitions above zero.

| Distance | 0–20 Hz | Positive | 20–50 Hz | Positive |
|---|---:|---:|---:|---:|
| Near | +3.706 dB | 92.0% | +2.106 dB | 92.0% |
| Mid | +0.382 dB | 54.7% | -0.013 dB | 49.3% |
| Far | +0.224 dB | 52.0% | +0.006 dB | 50.7% |

Near is consistent. Mid and far are not reliably separated from their paired
idle intervals by this basic energy statistic. Speed was not the main weakness:
the 0–20 Hz medians were +0.873 dB slow, +1.150 dB normal, and +1.260 dB fast.

## Standing versus seated

The supplied context was preserved as follows:

| Sessions | Posture | Hand height |
|---|---|---|
| 01, 02, 05 | Standing | High |
| 03, 04 | Seated | Low |

Pipeline C's 0–20 Hz median was +0.873 dB for standing/high and +1.194 dB for
seated/low. Therefore, the lower hand position did not degrade this simple
screen. This comparison is observational rather than causal because posture is
confounded with session and distance-dependent background variation.

## Pipeline D decision

With a ±20 Hz DC guard, +12 dB threshold, and minimum cluster size of 8 pixels:

| Class | Completely blank Pipeline D windows |
|---|---:|
| Clicking hand | 220/225 (97.8%) |
| Empty | 83/270 (30.7%) |

This parameterization is rejected for training. It suppresses the low-frequency
clicking response and sometimes retains large components in empty captures.
Keep A, B, and C for model comparison; redesign D only after inspecting the
scrolling pilot and training-fold distributions.

## Recommended next capture

Before recording sessions 02–05 for both scrolling classes, record only
`session01` for `left_horizontal_scroll` and `right_horizontal_scroll`, across
all three speeds and all three distances: 18 takes total. Use standing/high for
that session to remain consistent with the existing session context. Audit the
18 takes immediately. Continue the remaining four sessions only if mid/far
directional motion remains visible and the Q15 shift-4 clipping screen passes.

For later model development, also consider a stationary person/no-action
negative class. The current `empty` class contains no person, so it does not
teach the classifier to reject a present but motionless hand.

## Reproduce the export

```powershell
python3.11 .\export_model_windows.py `
  --input-root .\dataset\model-pilot\raw\fs2000 `
  --out .\dataset\model-pilot\windows-two-class `
  --train-sessions session01 session02 session03 `
  --validation-sessions session04 `
  --test-sessions session05 `
  --classes empty clicking_hand `
  --session-context .\dataset\model-pilot\session_context.json `
  --diff-shift 4 `
  --highpass-hz 10 `
  --dc-guard-hz 20 `
  --cluster-threshold-db 12 `
  --cluster-min-pixels 8
```

