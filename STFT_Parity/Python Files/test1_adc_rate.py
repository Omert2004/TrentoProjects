"""Read CRC-protected 1 Hz ADC-rate reports from AI_Phase firmware."""

from __future__ import annotations

import argparse
import sys
import time

from stft_protocol import RatePacket, StftFrameReader

SAMPLING_RATE_HZ = 4000.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM7")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--sampling-rate", type=float, default=SAMPLING_RATE_HZ)
    parser.add_argument("--duration", type=float, default=15.0)
    parser.add_argument("--rate-tolerance-percent", type=float, default=2.0)
    args = parser.parse_args()
    try:
        import serial
    except ModuleNotFoundError:
        print("Error: pyserial is required: pip install pyserial", file=sys.stderr)
        return 2

    port = serial.Serial(args.port, args.baud, timeout=0.25)
    port.reset_input_buffer()
    reader = StftFrameReader(port)
    rates: list[int] = []
    start = time.perf_counter()
    print(f"Connected to {args.port} @ {args.baud} baud.")
    try:
        while time.perf_counter() - start < args.duration:
            frame = reader.read_frame()
            if isinstance(frame, RatePacket):
                rates.append(frame.accepted_samples)
                pct = 100.0 * frame.accepted_samples / args.sampling_rate
                print(f"[{time.perf_counter()-start:6.2f}s] report={frame.report_sequence:5d}, accepted={frame.accepted_samples:5d} samples/s ({pct:5.1f}%), MCU drops={frame.cumulative_drop_count}")
    except KeyboardInterrupt:
        pass
    finally:
        port.close()

    stats = reader.stats
    average = sum(rates) / len(rates) if rates else 0.0
    error_pct = 100.0 * (average - args.sampling_rate) / args.sampling_rate
    passed = bool(rates) and abs(error_pct) <= args.rate_tolerance_percent and not stats.corruption_detected and stats.first_reported_drop_count in (None, 0)
    print(f"\nRate reports: {len(rates)}; validated columns skipped: {stats.columns_accepted}")
    print(f"Average accepted ADC rate: {average:.1f} samples/s ({error_pct:+.2f}%)")
    if rates:
        print(f"Min: {min(rates)}; max: {max(rates)}")
    print(f"Parser statistics: {stats.to_dict()}")
    print(f"Validation: {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
