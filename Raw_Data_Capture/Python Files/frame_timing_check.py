"""
frame_timing_check.py -- measures the actual time between consecutive
raw I/Q frame arrivals, to characterize a suspected periodic halt
objectively instead of eyeballing terminal scroll speed.

Same 6-byte, no-marker frame format as raw_print.py:
    [0xAA][0x55][IFI_lo][IFI_hi][IFQ_lo][IFQ_hi]

Reports:
  - overall throughput (frames/sec, matches expectation vs. not)
  - mean/median/max inter-frame gap
  - every gap above --threshold-ms, with its timestamp -- if these are
    evenly spaced, that's your "periodic" confirmed, and the spacing
    tells us what to go looking for (WDT period, USB polling interval,
    Python's own print() falling behind, etc.)

Usage:
    python frame_timing_check.py --port COM7 --duration 20
"""

import argparse
import struct
import time
import serial

SYNC1, SYNC2 = 0xAA, 0x55
FRAME_BODY_SIZE = 4
FRAME_TOTAL_SIZE = 6


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--port", default="COM7")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--duration", type=float, default=20.0)
    p.add_argument("--threshold-ms", type=float, default=5.0,
                    help="gaps larger than this are reported individually (default: 5ms)")
    return p.parse_args()


def read_frame(ser):
    b = ser.read(1)
    if not b or b[0] != SYNC1:
        return None
    b = ser.read(1)
    if not b or b[0] != SYNC2:
        return None
    body = ser.read(FRAME_BODY_SIZE)
    if len(body) != FRAME_BODY_SIZE:
        return None
    return struct.unpack("<HH", body)


def main():
    args = parse_args()
    ser = serial.Serial(args.port, args.baud, timeout=1)
    ser.reset_input_buffer()
    print(f"Connected to {args.port} @ {args.baud} baud. "
          f"Measuring for {args.duration}s (no per-frame printing -- "
          f"that's part of what we're testing for)...\n")

    gaps = []
    big_gaps = []   # (timestamp_since_start, gap_ms)
    resyncs = 0
    count = 0
    last_t = None

    start = time.time()
    while time.time() - start < args.duration:
        frame = read_frame(ser)
        now = time.time()

        if frame is None:
            resyncs += 1
            continue

        count += 1
        if last_t is not None:
            gap_ms = (now - last_t) * 1000.0
            gaps.append(gap_ms)
            if gap_ms > args.threshold_ms:
                big_gaps.append((now - start, gap_ms))
        last_t = now

    elapsed = time.time() - start
    ser.close()

    print(f"Frames received: {count}   Resync events: {resyncs}")
    print(f"Elapsed: {elapsed:.2f}s   Effective rate: {count / elapsed:.1f} frames/sec")

    theoretical_max = args.baud / 10 / FRAME_TOTAL_SIZE
    print(f"Theoretical max at this baud (6-byte frames, back-to-back): "
          f"{theoretical_max:.1f} frames/sec")

    if gaps:
        gaps_sorted = sorted(gaps)
        median = gaps_sorted[len(gaps_sorted) // 2]
        print(f"\nInter-frame gap: mean={sum(gaps)/len(gaps):.3f} ms  "
              f"median={median:.3f} ms  max={max(gaps):.3f} ms")

    if big_gaps:
        print(f"\n{len(big_gaps)} gap(s) over {args.threshold_ms} ms:")
        for t, g in big_gaps:
            print(f"  at t={t:7.3f}s   gap={g:8.3f} ms")

        if len(big_gaps) >= 2:
            intervals = [big_gaps[i+1][0] - big_gaps[i][0] for i in range(len(big_gaps) - 1)]
            print(f"\nSpacing between successive large gaps: "
                  f"mean={sum(intervals)/len(intervals):.3f}s  "
                  f"min={min(intervals):.3f}s  max={max(intervals):.3f}s")
            print("(if these are all close to the same value, the halt is genuinely periodic "
                  "at roughly that interval)")
    else:
        print(f"\nNo gaps over {args.threshold_ms} ms -- no halt detected at this threshold.")


if __name__ == "__main__":
    main()