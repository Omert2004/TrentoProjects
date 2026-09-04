# Raw_Data_Capture

This project receives a continuous, packetized 2000 Hz raw I/Q stream from the
MSP430FR5994 firmware in this directory. The C and Python files must be updated
together because they share the protocol below.

Use Python 3.10 or newer.

## Required coordinated update

Replace and rebuild **all three firmware files** in the CCS project before
using these Python tools:

- `main.c`
- `radar_configuration.c`
- `radar_configuration.h`

Then flash the MSP430 and use the Python files from this same folder. The old
six-byte firmware and this packet parser are intentionally incompatible.

`--sampling-rate 2000` is a validation input for Python; it does not configure
the MCU. The actual 2000 Hz rate is set by `SAMPLING_RATE_HZ` in
`radar_configuration.h` and the firmware timer derived from that constant.

## Wire protocol

Protocol v1 uses one packet for up to 32 I/Q sample pairs:

```text
AA 55 D4
packet_sequence:u16
first_sample_index:u32
sample_count:u8
cumulative_mcu_drop_count:u32
(IFI:u16, IFQ:u16) * sample_count
crc16:u16
```

All multi-byte values are little-endian. CRC16-CCITT-FALSE covers marker `D4`
through the last payload byte and uses initial value `0xFFFF`, polynomial
`0x1021`, no reflection, and no final XOR.

The shared `raw_protocol.py` parser preserves partial reads, handles overlapping
sync words, validates CRC and 12-bit ADC values, and checks packet sequence,
device sample sequence, and the cumulative MCU ring-drop counter.

A full packet is 144 bytes. At 2000 samples/s the stream uses 9000 B/s, or
78.1% of the 115200-baud 8-N-1 link. The packetized theoretical limit is 2560
samples/s. The earlier six-byte-per-sample protocol could carry only 1920
samples/s and must not be used with this Python version.

After flashing, a healthy `rate_check.py` run should report a 2560 sample/s
packetized ceiling, about 78.1% requested utilization, about 2000 validated
samples/s, no CRC/sequence/drop errors, and `PASS`.

## Setup

```bash
python -m pip install -r requirements.txt
```

## Recommended validation order

First verify the configured rate without printing every sample:

```bash
python3.11 rate_check.py --port COM7 --baud 115200 --sampling-rate 2000 --duration 15
```

Then check host-side stalls. These are USB/serial arrival measurements, not ADC
jitter measurements:

```bash
python3.11 frame_timing_check.py --port COM7 --baud 115200 --sampling-rate 2000 --duration 30
```

Capture a fixed-duration file:

```bash
python3.11 raw_serial_capture.py --port COM7 --baud 115200 \
  --sampling-rate 2000 --duration 10 --label no_movement --out captures
```

Every CSV receives a matching `.metadata.json` containing the configured rate,
observed receive rate, UART capacity, CRC and sequence statistics, and MCU drop
counter changes. A failed capture is still saved for diagnosis, but the command
exits with status 2 and says not to use it as a clean dataset.

For repeated interactive takes:

```bash
python3.11 raw_capture.py --port COM7 --sampling-rate 2000 --default-duration 2
```

## Analysis

Raw time series and I/Q constellation:

```bash
python3.11 visualizer.py captures/example.csv
```

Configurable raw-to-spectrogram processing:

```bash
python spectrogram_view.py captures/example.csv \
  --fft-size 256 --hop 128 --window hann --filter difference
```

Supported filters are `none`, `mean`, `difference`, and a first-order
`highpass` used with `--highpass-hz`.

Stationary-interference analysis uses an averaged complex Welch PSD. Separate
segments are never concatenated across capture gaps:

```bash
python interference_check.py captures/no_movement.csv \
  --target-frequency 700 --nperseg 4096
```

A frequency above Nyquist cannot be identified at its original frequency. For
example, at a 2000 Hz sampling rate, a 2000 Hz component aliases to DC.

Create deterministic raw windows without crossing segment boundaries:

```bash
python splitter.py captures/example.csv windows \
  --window-samples 2048 --stride-samples 1024
```

## Tests

```bash
python -m unittest discover -s tests -v
```

The tests inject overlapping sync, partial reads, CRC corruption, a deleted
byte, packet/sample sequence gaps, counter rollover, ADC-range corruption,
metadata mismatch, a known complex tone, and multiple independent segments.
