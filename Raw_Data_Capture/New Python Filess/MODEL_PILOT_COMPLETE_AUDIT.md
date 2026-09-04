# Complete Four-Class Model-Pilot Audit

## Decision

The uploaded archive is complete, internally consistent, and ready for
fixed-point feature generation. No validated main-session raw capture needs to
be repeated for the preliminary four-class model experiment.

The model experiment must still be described as provisional: `empty` and
`clicking_hand` were recorded on 2026-08-17, while both scrolling classes were
recorded on 2026-08-18. Class is therefore partly confounded with recording
day/background. The five session IDs are deterministic split groups, but they
are not independent collection days or independent subjects.

## Archive inventory

| Scope | CSV/metadata pairs |
|---|---:|
| Main sessions 01–05 | 150 |
| Earlier `scrollpilot01` | 18 |
| Alternating `directioncheck01` | 1 |
| **Total** | **169** |

- ZIP integrity: PASS; no compressed-data errors.
- ZIP SHA256:
  `8874ba3fd71c4fb526c1264f9e3c4b591e6d4e87fa8fedb9f14b376458140949`
- Main capture manifest: 168 rows, covering every raw capture under
  `model-pilot/raw` exactly once.
- Direction manifest: one row, resolving correctly.
- Missing, unreferenced, or duplicate raw files: zero.

## Main matrix

| Class | Captures | Marked actions |
|---|---:|---:|
| `empty` | 15 | 0 |
| `clicking_hand` | 45 | 225 |
| `left_horizontal_scroll` | 45 | 225 |
| `right_horizontal_scroll` | 45 | 225 |
| **Total** | **150** | **675** |

Every session contains 3 empty, 9 clicking, 9 left-scroll, and 9 right-scroll
captures. All 150 expected session/class/speed/distance cells are present
exactly once. Names, folders, metadata labels, class directions, speed targets,
and distance values agree.

## Integrity, transport, and timing

| Check | Result |
|---|---:|
| Main raw samples | 7,864,384 |
| CSV hashes matching metadata | 169/169 |
| Host transport validation | 169/169 PASS |
| Scientific sample continuity | 169/169 PASS |
| CRC/header/ADC-packet/resync errors | 0 |
| Packet/sample gaps, reorders, and MCU drops | 0 |
| CSV/metadata/parser sample-count mismatches | 0 |
| Discontinuous CSV sample indices | 0 |
| Captures with multiple segments | 0 |
| Main observed receive rate | 1992.12–1994.17 samples/s |
| Main receive-rate error | -0.394% to -0.292% |
| Main marker boundaries | 1,350 |
| Mean marker scheduling error | 8.52 ms |
| 95th-percentile marker error | 18.36 ms |
| Maximum marker error | 23.76 ms |

All clicking and scrolling recordings use the intended five one-second event
windows beginning at 3, 8, 13, 18, and 23 seconds. Slow, normal, and fast use
0.75, 0.50, and 0.25 second motion targets. Empty captures use the intended
20-second schedule.

There were 90 ADC rail samples across the 7,864,384 main samples (0.00114%),
with no capture containing more than seven. This is negligible and does not
invalidate a take.

## Q15 difference scaling

| Shift | Total clipped samples | Captures over 0.1% | Worst capture |
|---:|---:|---:|---:|
| 3 | 0 | 0 | 0% |
| **4** | **6** | **0** | **0.00372%** |
| 5 | 45,029 | 53 | 3.63995% |
| 6 | 501,133 | 61 | 39.12520% |

Every newly added left- and right-scroll capture has zero shift-4 clipping.
The six shift-4 samples are confined to five previously audited clicking-hand
captures. Shift 4 remains the correct fixed-point difference-filter gain.

## Signal-quality screen

The following is a diagnostic Pipeline-C screen, not the final MSP430 feature
export. It uses difference shift 4 and a floating-point 256-point Hann STFT to
compare each event with idle from the same capture. A fixed +256 ms marker
offset is shown as a reaction-time sensitivity check.

### Median 0–20 Hz event-minus-idle energy

| Class | Near | Mid | Far |
|---|---:|---:|---:|
| Clicking | +3.706 dB; 92.0% positive | +0.382 dB; 54.7% | +0.224 dB; 52.0% |
| Left scroll | +12.372 dB; 96.0% | +3.922 dB; 85.3% | +0.797 dB; 72.0% |
| Right scroll | +11.168 dB; 94.7% | +2.917 dB; 84.0% | +0.910 dB; 80.0% |

Near is clearly the strongest operating range for all three actions. Scrolling
retains useful low-frequency energy at mid. Far energy is weak, especially
above 50 Hz. Clicking at mid and far is close to its paired idle background.

The low-frequency result is important: do not remove the central ±20 Hz bins
from the model input. The rejected Pipeline-D DC guard erased most clicking
windows. Difference filtering already performs the intended clutter removal.

Seated/low recordings are not weaker. Event-positive percentages were slightly
higher seated than standing for clicking and both scrolling classes, supporting
the intended driver-seat use case. This remains observational because posture
and session are not independently randomized.

## Left/right direction risk

Simple signed-Doppler screens remain modest:

- Main dataset, near, signed temporal-sequence AUC: 0.608.
- Main dataset, all distances, signed temporal-sequence AUC: 0.537.
- Earlier within-recording alternating check: AUC 0.72.

This does not prove a neural model cannot distinguish direction, but it means
high left/right accuracy should be examined carefully for recording-order or
background shortcuts. The model comparison is the correct next experiment.

## Automated software tests

| Suite | Result |
|---|---:|
| Raw capture and preprocessing | 23/23 PASS |
| AI-phase STFT host protocol/data | 13/13 PASS |
| Fixed-point parity helpers | 5/5 PASS |
| **Total** | **41/41 PASS** |

The AI-phase tests initially encountered a corrupted generated `.pyc` cache.
After moving that cache aside, all tests passed from the Python sources. No
source-code defect was found.

## Recommended balanced extraction

Each timed gesture already provides 225 labeled actions. Empty has no marked
actions. The current floating-point exporter can produce 270 empty windows,
but for the first balanced four-class experiment select exactly 15 deterministic
empty windows per raw empty capture:

| Split | Sessions | Samples per class | Four-class total |
|---|---|---:|---:|
| Train | 01–03 | 135 | 540 |
| Validation | 04 | 45 | 180 |
| Test | 05 | 45 | 180 |
| **Total** | | **225** | **900** |

All five repetitions from a source capture stay in its session split. Never
randomly split individual event windows across train, validation, and test.

## Next feature-generation stage

Before training:

1. Update the embedded configuration to 2 kHz, difference filtering enabled,
   and `DIFF_SHIFT=4`.
2. Match the host exporter to the MSP430 pipeline: Q15 difference, quantized
   Q15 Hann, `msp_cmplx_fft_fixed_q15`, integer magnitude-squared/log2 output,
   and the board's 0–31 feature range.
3. Preserve all 256 frequency bins, including 0–20 Hz.
4. Compare marker-centered and fixed +256 ms extraction using training and
   validation only. Do not use session05 to choose alignment.
5. Dump one or more STFT tensors from the MSP430 and run bin-level parity
   checks before exporting the complete dataset.
6. Freeze tensor orientation (`15x256` on board versus `256x15` in host code),
   scaling, and model input dtype.
7. Export the balanced 900-sample dataset and verify every tensor, manifest
   path, label, and split.
8. Train and tune with sessions01–04. Open session05 only after preprocessing
   and model settings are frozen.

The first model result is valuable, but because recording day is confounded
with class, it must not yet be treated as deployment-level generalization. A
later confirmation session should record all four classes under the same
seated setup and recording occasion.
