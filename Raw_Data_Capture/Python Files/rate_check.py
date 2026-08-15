"""
rate_check.py -- verifies the FR5994 firmware is streaming spectrogram
columns at the expected rate, with no plotting/FFT: just counts, timing,
and resync events.

NOTE: this replaces an older version of this script that parsed the
firmware's original raw 6-byte IFI/IFQ frame format. Current firmware
streams finished spectrogram columns instead (see STFT.c), multiplexed
with two other frame types on the same UART stream:
    0xC0  spectrogram column   (256-byte body)  -- what this script counts
    0xC2  1 Hz ADC-rate snapshot (2-byte body)   -- skipped, not this script's job
    0xC3  1 Hz STFT/DMA profile  (6-byte body)   -- skipped, not this script's job

Expected column rate = SAMPLING_RATE_HZ / FFT_HOP (default 4000/128 = 31.25 Hz).

Usage:
    python3 rate_check.py --port COM7 --baud 115200 --duration 5
"""

import argparse
import struct
import time
import serial

SYNC1, SYNC2 = 0xAA, 0x55
SPECTROGRAM_MARKER = 0xC0
RATE_MARKER = 0xC2
PROFILE_MARKER = 0xC3
COLUMN_SIZE = 256          # FFT_SIZE
SAMPLING_RATE_HZ = 4000    # must match radar_configuration.h
FFT_HOP = 128               # must match STFT.h


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--port", default="COM7")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--duration", type=float, default=5.0, help="Seconds to measure")
    return p.parse_args()


def read_frame(ser):
    """Returns ('column', None) / ('rate', None) / ('profile', None), or
    None on desync/timeout (missing sync bytes, unrecognized marker, or a
    short read partway through a body)."""
    b = ser.read(1)
    if not b or b[0] != SYNC1:
        return None
    b = ser.read(1)
    if not b or b[0] != SYNC2:
        return None
    b = ser.read(1)
    if not b:
        return None
    marker = b[0]

    if marker == SPECTROGRAM_MARKER:
        body = ser.read(COLUMN_SIZE)
        if len(body) != COLUMN_SIZE:
            return None
        return ("column", None)
    elif marker == RATE_MARKER:
        body = ser.read(2)
        if len(body) != 2:
            return None
        return ("rate", None)
    elif marker == PROFILE_MARKER:
        body = ser.read(6)
        if len(body) != 6:
            return None
        return ("profile", None)
    else:
        return None  # unrecognized marker -- treat as desync


def main():
    args = parse_args()
    ser = serial.Serial(args.port, args.baud, timeout=1)
    ser.reset_input_buffer()
    print(f"Connected to {args.port} @ {args.baud} baud. Measuring for {args.duration}s...")

    columns = 0
    resyncs = 0
    gaps = []
    last_column_time = None
    start = time.time()

    while time.time() - start < args.duration:
        frame = read_frame(ser)
        if frame is None:
            resyncs += 1
            continue
        kind, _ = frame
        if kind != "column":
            continue  # rate/profile frames: consumed above, not counted here

        now = time.time()
        if last_column_time is not None:
            gaps.append((now - last_column_time) * 1000.0)  # ms
        last_column_time = now
        columns += 1

    elapsed = time.time() - start
    ser.close()

    target_hz = SAMPLING_RATE_HZ / FFT_HOP
    target_gap_ms = 1000.0 / target_hz

    print(f"\nSpectrogram columns received: {columns}")
    print(f"Elapsed: {elapsed:.3f}s")
    print(f"Effective column rate: {columns / elapsed:.2f} Hz  "
          f"(target: {target_hz:.2f} Hz, i.e. {SAMPLING_RATE_HZ}/{FFT_HOP})")
    print(f"Resync events (lost frame alignment): {resyncs}")
    if gaps:
        print(f"Inter-column gap: mean={sum(gaps)/len(gaps):.1f} ms  "
              f"min={min(gaps):.1f} ms  max={max(gaps):.1f} ms")
    print(f"Expected gap at {SAMPLING_RATE_HZ} Hz / hop {FFT_HOP}: {target_gap_ms:.1f} ms")

    if resyncs > 0:
        print(f"\n{resyncs} resync event(s) is not zero -- worth investigating before "
              f"logging a real dataset. With ~{target_gap_ms:.0f} ms between columns and a "
              f"1s read timeout, a resync here means bytes were actually lost or corrupted, "
              f"not just delayed.")


if __name__ == "__main__":
    main()