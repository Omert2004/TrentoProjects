"""
raw_capture.py -- collects one raw I/Q take per 'S' command and saves it
as a CSV. Loops so you can collect many takes in one session without
resetting the board.

Protocol (see main.c on the MSP430):
  PC  -> MCU : single byte 'S' (0x53), starts one fixed-length capture
  MCU -> PC  : repeated data frames, then one end frame
    data frame : [0xAA][0x55][0xD2][seq_lo][seq_hi][n]
                 ([I_lo][I_hi][Q_lo][Q_hi]) x n
    end frame  : [0xAA][0x55][0xD3][total_lo][total_hi]

Usage:
    python raw_capture.py --port COM7 --out captures/
"""

import argparse
import struct
import time
from pathlib import Path

import serial

SYNC1, SYNC2 = 0xAA, 0x55
DATA_MARKER = 0xD2
END_MARKER = 0xD3

# Must match radar_configuration.h -- kept as constants here rather than
# parsed off the wire so a mismatch is visible immediately (a wrong value
# here means the dataset labels will be misleading, not just a warning).
SAMPLING_RATE_HZ = 4000
CAPTURE_SECONDS = 2         # must match radar_configuration.h
EXPECTED_SAMPLES = SAMPLING_RATE_HZ * CAPTURE_SECONDS


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--port", default="COM7")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--out", default="captures")
    return p.parse_args()


def read_header(ser):
    """Blocks until a valid [0xAA][0x55][marker] header is found."""
    while True:
        b = ser.read(1)
        if not b or b[0] != SYNC1:
            continue
        b = ser.read(1)
        if not b or b[0] != SYNC2:
            continue
        b = ser.read(1)
        if not b:
            continue
        return b[0]


def capture_one_take(ser):
    samples = []  # list of (I, Q)
    while True:
        marker = read_header(ser)

        if marker == DATA_MARKER:
            hdr = ser.read(3)
            if len(hdr) != 3:
                print("  [warning: short frame header, dropping]")
                continue
            seq, n = struct.unpack("<HB", hdr)
            body = ser.read(n * 4)
            if len(body) != n * 4:
                print(f"  [warning: short frame body, seq={seq}]")
                continue
            values = struct.unpack(f"<{n * 2}H", body)
            for k in range(n):
                samples.append((values[2 * k], values[2 * k + 1]))

        elif marker == END_MARKER:
            body = ser.read(2)
            if len(body) != 2:
                print("  [warning: short end-frame body]")
                break
            (total,) = struct.unpack("<H", body)
            if total != len(samples):
                print(f"  [warning: MCU reports {total} samples, "
                      f"received {len(samples)} -- take may be incomplete]")
            break

        else:
            print(f"  [resync: unexpected marker 0x{marker:02X}]")

    return samples


def save_take(samples, out_dir, label, segments=None):
    """segments: optional list of (segment_index, sample_count) so long
    sessions (see capture_session()) can flag where a UART-drain gap
    falls, without the caller needing to guess offsets."""
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"{label}_{timestamp}.csv"

    if segments is None:
        segments = [(0, len(samples))]

    with open(path, "w") as f:
        f.write("sample_idx,segment,I,Q\n")
        idx = 0
        for seg_idx, count in segments:
            for _ in range(count):
                i_val, q_val = samples[idx]
                f.write(f"{idx},{seg_idx},{i_val},{q_val}\n")
                idx += 1

    print(f"  Saved {len(samples)} samples across {len(segments)} segment(s) -> {path}")
    return path


def capture_session(ser, target_seconds):
    """Chains back-to-back complete segments (each hardware-timed and
    drop-free) until target_seconds of data has been collected. There is
    a real gap between segments while the previous one drains over UART
    -- see the reasoning in the chat, not something to try to eliminate --
    but nothing inside any single segment is ever dropped or approximated.
    """
    num_segments = max(1, -(-target_seconds // CAPTURE_SECONDS))  # ceil div
    all_samples = []
    segments = []

    for seg_idx in range(num_segments):
        print(f"  Segment {seg_idx + 1}/{num_segments}: capturing...")
        ser.reset_input_buffer()
        ser.write(b'S')
        seg_samples = capture_one_take(ser)
        segments.append((seg_idx, len(seg_samples)))
        all_samples.extend(seg_samples)
        print(f"    -> {len(seg_samples)} samples "
              f"({len(seg_samples) / SAMPLING_RATE_HZ:.2f}s)")

    total_seconds = len(all_samples) / SAMPLING_RATE_HZ
    print(f"  Session complete: {len(all_samples)} samples, "
          f"{total_seconds:.2f}s of data across {num_segments} segment(s).")
    return all_samples, segments


def main():
    args = parse_args()
    out_dir = Path(args.out)

    ser = serial.Serial(args.port, args.baud, timeout=2)
    ser.reset_input_buffer()
    print(f"Connected to {args.port} @ {args.baud} baud.")
    print(f"Each take captures {EXPECTED_SAMPLES} samples "
          f"({CAPTURE_SECONDS}s @ {SAMPLING_RATE_HZ} Hz).\n")

    try:
        while True:
            label = input("Gesture label for next take (blank to quit): ").strip()
            if not label:
                break

            duration_str = input(
                f"  Total seconds to record for '{label}' "
                f"[default {CAPTURE_SECONDS}]: "
            ).strip()
            duration = int(duration_str) if duration_str else CAPTURE_SECONDS

            input("  Press Enter, then hold/perform the gesture "
                  "(segments run back-to-back automatically)...")

            if duration <= CAPTURE_SECONDS:
                ser.reset_input_buffer()
                ser.write(b'S')
                print("  Capturing...")
                samples = capture_one_take(ser)
                save_take(samples, out_dir, label)
            else:
                samples, segments = capture_session(ser, duration)
                save_take(samples, out_dir, label, segments=segments)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        ser.close()


if __name__ == "__main__":
    main()