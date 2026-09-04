"""Validate AI_Phase STFT column rate and transport integrity."""

from __future__ import annotations

import argparse
import statistics
import sys
import time

from stft_protocol import ColumnPacket, StftFrameReader, serial_capacity_columns_per_second

SAMPLING_RATE_HZ = 4000.0
FFT_HOP = 128


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM7")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--sampling-rate", type=float, default=SAMPLING_RATE_HZ)
    parser.add_argument("--duration", type=float, default=15.0)
    parser.add_argument("--rate-tolerance-percent", type=float, default=2.0)
    args = parser.parse_args()
    if args.baud <= 0 or args.sampling_rate <= 0 or args.duration <= 0:
        print("Error: baud, sampling rate, and duration must be positive.", file=sys.stderr)
        return 2

    try:
        import serial
    except ModuleNotFoundError:
        print("Error: pyserial is required: pip install pyserial", file=sys.stderr)
        return 2

    port = serial.Serial(args.port, args.baud, timeout=0.25)
    port.reset_input_buffer()
    reader = StftFrameReader(port)
    target_hz = args.sampling_rate / FFT_HOP
    print(f"Connected to {args.port} @ {args.baud} baud. Measuring {args.duration:g}s...")

    first_time = None
    last_time = None
    arrival_times: list[int] = []
    deadline = time.perf_counter() + args.duration
    try:
        while time.perf_counter() < deadline:
            frame = reader.read_frame()
            if isinstance(frame, ColumnPacket):
                if first_time is None:
                    first_time = frame.host_time_ns
                last_time = frame.host_time_ns
                arrival_times.append(frame.host_time_ns)
    except KeyboardInterrupt:
        pass
    finally:
        port.close()

    elapsed = ((last_time - first_time) / 1e9) if first_time is not None and last_time != first_time else args.duration
    intervals = [(b - a) / 1e6 for a, b in zip(arrival_times, arrival_times[1:])]
    observed = (len(arrival_times) - 1) / elapsed if len(arrival_times) > 1 else 0.0
    error_pct = 100.0 * (observed - target_hz) / target_hz
    stats = reader.stats
    no_prior_drops = stats.first_reported_drop_count in (None, 0)
    passed = bool(arrival_times) and abs(error_pct) <= args.rate_tolerance_percent and not stats.corruption_detected and no_prior_drops

    print(f"\nValidated columns: {len(arrival_times)}")
    print(f"Observed column rate: {observed:.3f} Hz")
    print(f"Target column rate: {target_hz:.3f} Hz")
    print(f"Rate error: {error_pct:+.2f}%")
    print(f"UART column-only ceiling: {serial_capacity_columns_per_second(args.baud):.2f} columns/s")
    if intervals:
        ordered = sorted(intervals)
        p95 = ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]
        print(f"Host inter-arrival: mean={statistics.fmean(intervals):.2f} ms, p95={p95:.2f} ms, max={max(intervals):.2f} ms")
    print(f"Parser statistics: {stats.to_dict()}")
    print(f"Validation: {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())

