# AI_Phase 4 kHz vs 2 kHz Sampling-Rate Experiment

## Purpose

This experiment evaluates whether the `AI_Phase` continuous on-chip STFT pipeline should use a 4 kHz or 2 kHz ADC sampling rate for hand-gesture recognition on the MSP430FR5994.

The comparison focuses on:

- ADC and STFT column rates
- UART and parser integrity
- MCU processing load
- Doppler range and frequency resolution
- Temporal coverage of a model input
- Visibility of fast, normal, and slow hand motions
- Evidence of Doppler aliasing at the FFT boundaries

The official comparison files are:

- `horizontal-slide_mid_session001.*` — 4 kHz reference capture
- `horizontal-slide_mid_session003.*` — 2 kHz validated capture

`session002` is not part of the official comparison because its Python recorder still expected a 4 kHz stream. Its firmware stream was continuous, but its metadata was marked invalid by the incorrect host-side rate target.

## Shared STFT Configuration

Both experiments used the same FFT dimensions:

```c
#define FFT_SIZE 256
#define FFT_HOP 128
```

Clutter cancellation was disabled during these captures.

| Property | 4 kHz | 2 kHz |
|---|---:|---:|
| Sampling rate | 4000 Hz | 2000 Hz |
| Nyquist Doppler range | -2000 to +1984.375 Hz | -1000 to +992.1875 Hz |
| Frequency-bin spacing | 15.625 Hz/bin | 7.8125 Hz/bin |
| FFT-window duration | 64 ms | 128 ms |
| Hop duration | 32 ms | 64 ms |
| Target STFT column rate | 31.25 columns/s | 15.625 columns/s |
| Columns spanning approximately 1.024 s | 31 | 15 |

For `N` consecutive STFT columns, temporal coverage is:

```text
(FFT_SIZE + (N - 1) * FFT_HOP) / sampling_rate
```

Therefore:

```text
4 kHz: (256 + 30 * 128) / 4000 = 1.024 s using 31 columns
2 kHz: (256 + 14 * 128) / 2000 = 1.024 s using 15 columns
```

## Official Output Files

### Session 001 — 4 kHz

Data and metadata:

```text
horizontal-slide_mid_session001.txt
horizontal-slide_mid_session001.metadata.json
```

Generated visualizations:

```text
horizontal-slide_mid_session001_raw.png
horizontal-slide_mid_session001_view.png
```

The capture contained 1,739 validated STFT columns over approximately 55.90 seconds.

### Session 003 — 2 kHz

Data and metadata:

```text
horizontal-slide_mid_session003.txt
horizontal-slide_mid_session003.metadata.json
```

Generated visualizations:

```text
horizontal-slide_mid_session003_raw.png
horizontal-slide_mid_session003_view.png
```

The capture contained 497 validated STFT columns over approximately 32.06 seconds.

## Firmware and Host Configuration

### 4 kHz configuration

```c
#define SAMPLING_RATE_HZ 4000
```

Python validation tools were run with:

```text
--sampling-rate 4000
```

### 2 kHz configuration

```c
#define SAMPLING_RATE_HZ 2000
```

Python validation and capture tools were run with:

```text
--sampling-rate 2000
```

The profiling clock remained:

```python
ACLK_HZ = 32768.0
```

`ACLK_HZ` is the MSP430 profiling-timer reference. It is independent of the ADC sampling rate and must not be changed to 2000 or 4000.

## Validation Commands

### ADC rate

```powershell
python3.11 .\test1_adc_rate.py --port COM7 --baud 115200 --sampling-rate <RATE> --duration 15
```

### STFT column rate

```powershell
python3.11 .\rate_check.py --port COM7 --baud 115200 --sampling-rate <RATE> --duration 15
```

### MCU profiling

Set `SAMPLING_RATE_HZ` in `profile_check.py` to the active firmware rate, then run:

```powershell
python3.11 .\profile_check.py --port COM7 --baud 115200 --duration 15
```

### Dataset capture

```powershell
python3.11 .\radar_dataset_capture.py --port COM7 --sampling-rate <RATE> --gesture-class horizontal_slide --distance mid --duration-min 1
```

### Visualization

```powershell
python3.11 .\visualizer.py <CAPTURE_FILE>.txt
python3.11 .\spectrogram_view.py <CAPTURE_FILE>.txt
```

## Measured Results

| Measurement | Session 001: 4 kHz | Session 003: 2 kHz |
|---|---:|---:|
| Configured ADC rate | 4000 Hz | 2000 Hz |
| Measured ADC rate | approximately 3989.6 samples/s | approximately 1994.9 samples/s |
| ADC-rate error | approximately -0.26% | approximately -0.26% |
| Observed column rate | 31.160 Hz | 15.587 Hz |
| Dataset column-rate error | -0.29% | -0.24% |
| Validated columns | 1,739 | 497 |
| Capture duration | 55.90 s | 32.06 s |
| CRC errors | 0 | 0 |
| Invalid packets | 0 | 0 |
| Missing columns | 0 | 0 |
| Missing samples | 0 | 0 |
| Sequence reorders | 0 | 0 |
| MCU drop increase | 0 | 0 |
| Capture validation | PASS | PASS |

Both rates produced scientifically continuous data. Neither experiment showed transport loss, parser corruption, or MCU sampling drops.

## MCU Processing Result

At 4 kHz, the firmware produced approximately 31 to 32 STFT hops per second. STFT processing required approximately 17.74 ms per hop, or about 550 ms of processing time per second.

At 2 kHz, the firmware produced approximately 15 to 16 STFT hops per second. STFT processing required approximately 17.30 ms per hop, or about 260 to 277 ms of processing time per second.

The FFT cost per hop is nearly unchanged because the FFT size remains 256. Reducing the sampling rate halves the number of hops per second and therefore approximately halves the continuous STFT processing load.

Approximate processing utilization:

| Rate | Hop interval | STFT time per hop | Approximate STFT utilization |
|---|---:|---:|---:|
| 4 kHz | 32 ms | 17.74 ms | 55% |
| 2 kHz | 64 ms | 17.30 ms | 27% |

DMA waiting remained effectively zero in both configurations.

## Gesture Procedure for Session 003

For the 2 kHz capture, the subject first entered the radar field, performed the gestures, and then left the radar field.

The intended performance order was:

1. Speed: fast
   - left
   - right
   - up
   - down
2. Speed: normal
   - left
   - right
   - up
   - down
3. Speed: slow
   - left
   - right
   - up
   - down

Approximate event centers identified offline are:

| Event | Approximate time |
|---|---:|
| Enter radar field | 2.2-3.9 s |
| Fast left | 4.86 s |
| Fast right | 5.70 s |
| Fast up | 8.19 s |
| Fast down | 9.92 s |
| Normal left | 11.46 s |
| Normal right | 13.38 s |
| Normal up | 16.83 s |
| Normal down | 19.65 s |
| Slow left | 21.12 s |
| Slow right | 23.23 s |
| Slow up | 24.51 s |
| Slow down | 26.56 s |
| Leave radar field | 28.3-31.0 s |

The first two fast gestures may partially overlap the end of the entry movement. These approximate times are suitable for experiment review but should not be treated as automatic ground-truth labels without manual confirmation.

## Doppler-Boundary Analysis

### 4 kHz reference

The shifted FFT covers approximately -2000 to +1984 Hz. Offline inspection of `session001` found that most detected motion energy remained well inside this range. Approximately 95% of the dynamic energy was below about 830 Hz in the analyzed gesture events, with no convincing narrow-band wrap from one FFT boundary to the other.

### 2 kHz experiment

The shifted FFT covers approximately -1000 to +992 Hz. In `session003`, the widest identified fast gesture was the fast-up event. Approximately 95% of its dynamic energy remained below about 633 Hz. The remaining gestures were generally narrower.

The 2 kHz capture therefore did not show convincing Doppler aliasing for the performed motions. Bright structures at the outer FFT bins should not automatically be interpreted as lost data because persistent edge background and short broadband transients can illuminate those bins. Transport counters independently showed zero sample and packet loss.

## Interpretation

### Advantages of 4 kHz

- Approximately twice the temporal STFT update rate
- Twice the unaliased Doppler range
- More margin for unusually fast or close-range movements
- 31 columns across approximately 1.024 seconds

### Advantages of 2 kHz

- Twice the Doppler-frequency resolution per bin
- Fifteen columns naturally cover approximately 1.024 seconds
- Approximately half the STFT processing utilization
- Lower UART traffic
- No observed aliasing in the validated test gestures

## Dataset Limitations

The session files contain long continuous recordings, not one independently labeled example per gesture. Their metadata describes the overall class as `horizontal_slide`; individual direction and speed labels are not embedded in each STFT column.

For final supervised training:

- Record each direction/speed combination separately, or add explicit event markers.
- Wait at least two seconds after entering the radar field before the first gesture.
- Leave a consistent still interval between gestures.
- Wait at least two seconds after the last gesture before leaving.
- Record a `background` or `no_gesture` class.
- Preserve raw continuous captures even when derived 15-column or 31-column windows are generated.
- Split training, validation, and test data by recording session or participant, not by randomly mixing overlapping windows from one session.

## Current Decision

The experiment establishes that both 4 kHz and 2 kHz are technically valid for the current gesture set.

The 2 kHz configuration is a strong candidate because it provides a natural 15-column, approximately one-second model input and substantially reduces processing load without observed aliasing in `session003`.

The 4 kHz configuration remains the safer choice for maximum temporal detail and Doppler headroom. A final sampling-rate decision should be based on matched, labeled datasets and classification accuracy rather than on a single visualization.

Recommended next step:

1. Capture matched 2 kHz and 4 kHz sessions using identical gesture order, distance, timing, and subject position.
2. Segment them into equal-duration examples.
3. Train the same baseline model for both rates.
4. Compare validation accuracy, confusion matrices, robustness, and MCU cost.

## Experiment Status

- 4 kHz continuous acquisition: validated
- 2 kHz continuous acquisition: validated
- UART and sequence integrity: validated at both rates
- 4 kHz official reference: `session001`
- 2 kHz official comparison: `session003`
- Final sampling-rate selection: pending matched classification experiment
